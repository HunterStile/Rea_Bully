#include "ReaBullyWorldProp.h"
#include "ReaBullyWorld.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMesh.h"
#include "PhysicsEngine/BodySetup.h"
#include "Interfaces/Interface_CollisionDataProvider.h"
#include "ReferenceSkeleton.h"
#include "StaticMeshResources.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Engine/Level.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "InputCoreTypes.h"
#include "Kismet/GameplayStatics.h"

namespace
{
TWeakObjectPtr<APlayerController> LastPropInputController;
uint64 LastPropInputFrame = MAX_uint64;

bool HasApprovedInteraction(const UReaBullyWorldPropData* Prop, const UReaBullyPropInteractionData* Action,
    const FString& SourceId)
{
    // These DAT triggers explicitly instantiate BikeGar and are called by the
    // global main script. SBikeGar and unrelated IPB copies are not this action.
    if (!Prop || !Prop->Mesh || !Action || Prop->AmbientLoop ||
        Prop->ModelKey != TEXT("10121_bikegar") || Action->ModelKey != Prop->ModelKey ||
        Action->SourceSHA256 != Prop->SourceSHA256 || Action->EvidenceSHA256.Len() != 64 ||
        (SourceId != TEXT("tbusines_doors.dat:23") && SourceId != TEXT("tBMX.dat:23")) ||
        Action->ClosedOriginal != TEXT("BIKEGAR\\IDLE") || Action->OpenOriginal != TEXT("BIKEGAR\\OPEN") ||
        Action->CloseOriginal != TEXT("BIKEGAR\\CLOSE") ||
        Action->ClosedPoseCenterCm.ContainsNaN() || Action->ClosedPoseExtentCm.ContainsNaN() ||
        Action->ClosedPoseExtentCm.GetMin() <= 0.0f) return false;
    for (const UAnimSequence* Clip : {Action->ClosedClip.Get(), Action->OpenClip.Get(), Action->CloseClip.Get()})
        if (!Clip || Clip->GetSkeleton() != Prop->Mesh->GetSkeleton() ||
            !FMath::IsFinite(Clip->GetPlayLength()) || Clip->GetPlayLength() <= 0.0f) return false;
    return true;
}

bool HasApprovedAmbientPolicy(const UReaBullyWorldPropData* Data)
{
    if (!Data->AmbientLoop) return Data->AmbientLoopOriginal.IsEmpty();
    if (Data->AmbientLoop->GetSkeleton() != Data->Mesh->GetSkeleton()) return false;
    if (Data->ModelKey == TEXT("10071_tgkflag"))
        return Data->AmbientLoopOriginal == TEXT("TGKFLAG\\IDLE");
    if (Data->ModelKey == TEXT("10220_carni01") || Data->ModelKey == TEXT("10222_carni02") ||
        Data->ModelKey == TEXT("10223_carni03"))
        return Data->AmbientLoopOriginal == TEXT("CARNI01\\CARNY_IDLE");
    return false;
}

bool HasApprovedGesture(const UReaBullyWorldPropData* Prop, const UReaBullyPropGestureData* Gesture,
    const FString& SourceId)
{
    if (!Prop || !Prop->Mesh || !Gesture || Gesture->ModelKey != Prop->ModelKey ||
        Gesture->SourceSHA256 != Prop->SourceSHA256 || Gesture->EvidenceSHA256.Len() != 64 ||
        Prop->AmbientLoop != Gesture->IdleClip || Gesture->IdleOriginal != TEXT("CARNI01\\CARNY_IDLE") ||
        Gesture->GestureOriginal != TEXT("CARNI01\\CARNY_CMERE") ||
        Gesture->IdlePoseCenterCm.ContainsNaN() || Gesture->IdlePoseExtentCm.ContainsNaN() ||
        Gesture->IdlePoseExtentCm.GetMin() <= 0.0f) return false;
    // Exact original IPB instances; the activation is a reconstructed manual control.
    const bool ApprovedSource =
        (Prop->ModelKey == TEXT("10220_carni01") &&
            (SourceId == TEXT("ipb:zone_carn1:26") || SourceId == TEXT("ipb:zone_carn1:36"))) ||
        (Prop->ModelKey == TEXT("10222_carni02") && SourceId == TEXT("ipb:zone_carn1:27")) ||
        (Prop->ModelKey == TEXT("10223_carni03") &&
            (SourceId == TEXT("ipb:zone_carn1:28") || SourceId == TEXT("ipb:zone_carn1:29") ||
             SourceId == TEXT("ipb:zone_carn1:30")));
    if (!ApprovedSource) return false;
    for (const UAnimSequence* Clip : {Gesture->IdleClip.Get(), Gesture->GestureClip.Get()})
        if (!Clip || Clip->GetSkeleton() != Prop->Mesh->GetSkeleton() ||
            !FMath::IsFinite(Clip->GetPlayLength()) || Clip->GetPlayLength() <= 0.0f) return false;
    return true;
}
}

AReaBullyWorldProp::AReaBullyWorldProp()
{
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.bStartWithTickEnabled = false;
    MeshComponent = CreateDefaultSubobject<USkeletalMeshComponent>(TEXT("OriginalObjectSkin"));
    RootComponent = MeshComponent;
    MeshComponent->SetMobility(EComponentMobility::Movable);
    MeshComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    MeshComponent->SetGenerateOverlapEvents(false);
}

bool AReaBullyWorldProp::ConfigureRecoveredProp(UReaBullyWorldPropData* Source)
{
    bInteractionConfigured = bInteractionMoving = bInteractionOpen = bInteractionTargetOpen = false;
    InteractionClip = nullptr;
    InteractionCount = 0;
    CompletedGestureCount = 0;
    LastGestureEndpointSeconds = 0.0f;
    SetActorTickEnabled(false);
    if (!Source || !Source->Mesh || !Source->Mesh->GetSkeleton() ||
        Source->SourceSHA256.Len() != 64 || !HasApprovedAmbientPolicy(Source) ||
        (InteractionData && GestureData) ||
        (InteractionData && !HasApprovedInteraction(Source, InteractionData, InteractionSourceId)) ||
        (GestureData && !HasApprovedGesture(Source, GestureData, InteractionSourceId)))
    {
        SetActorHiddenInGame(true);
        return false;
    }
    Data = Source;
    MeshComponent->SetSkeletalMeshAsset(Data->Mesh);
    MeshComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    MeshComponent->SetSimulatePhysics(false);
    MeshComponent->SetAnimationMode(EAnimationMode::AnimationSingleNode);
    MeshComponent->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::OnlyTickPoseWhenRendered;
    MeshComponent->SetAnimation(Data->AmbientLoop);
    if (Data->AmbientLoop) MeshComponent->Play(true);
    else MeshComponent->Stop();
    MeshComponent->RefreshBoneTransforms();
    if (InteractionData)
    {
        MeshComponent->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
        bInteractionConfigured = true;
        SetInteractionPose(InteractionData->ClosedClip, 0.0f);
        SetActorTickEnabled(true);
    }
    else if (GestureData)
    {
        MeshComponent->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
        bInteractionConfigured = true;
        SetActorTickEnabled(true);
    }
    SetActorHiddenInGame(false);
    return true;
}

void AReaBullyWorldProp::OnConstruction(const FTransform& Transform)
{
    Super::OnConstruction(Transform);
    if (Data) ConfigureRecoveredProp(Data);
}

void AReaBullyWorldProp::BeginPlay()
{
    Super::BeginPlay();
    if (!ConfigureRecoveredProp(Data))
        UE_LOG(LogTemp, Error, TEXT("Original prop has missing assets or unapproved playback policy: %s"), *GetName());
}

void AReaBullyWorldProp::SetInteractionPose(UAnimSequence* Clip, float Seconds)
{
    if (InteractionClip != Clip)
    {
        InteractionClip = Clip;
        MeshComponent->SetAnimation(Clip);
        if (auto* Instance = MeshComponent->GetSingleNodeInstance()) Instance->SetLooping(false);
    }
    MeshComponent->SetPosition(Seconds, false);
    MeshComponent->Stop();
    MeshComponent->TickAnimation(0.0f, false);
    MeshComponent->RefreshBoneTransforms();
}

bool AReaBullyWorldProp::IsWorldReadyForInteraction() const
{
    if (!GetWorld()) return false;
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It)
        if (!It->IsReady()) return false;
    return true;
}

bool AReaBullyWorldProp::RequestInteractionToggle()
{
    if (!bInteractionConfigured || !bExplorationInteractionEnabled || bInteractionMoving ||
        IsHidden() || !GetLevel() || !GetLevel()->bIsVisible || !IsWorldReadyForInteraction()) return false;
    bInteractionTargetOpen = !bInteractionOpen;
    bInteractionMoving = true;
    InteractionSeconds = 0.0f;
    SetInteractionPose(GestureData ? GestureData->GestureClip.Get() :
        (bInteractionTargetOpen ? InteractionData->OpenClip.Get() : InteractionData->CloseClip.Get()), 0.0f);
    ++InteractionCount;
    return true;
}

FVector AReaBullyWorldProp::GetInteractionWorldLocation() const
{
    if (GestureData) return GetActorTransform().TransformPosition(GestureData->IdlePoseCenterCm);
    return InteractionData ? GetActorTransform().TransformPosition(InteractionData->ClosedPoseCenterCm) : GetActorLocation();
}

float AReaBullyWorldProp::InteractionDistanceSquared(FVector Position) const
{
    if (!InteractionData && !GestureData) return MAX_flt;
    // One distance and nearest-input rule shared by garages and carnival gestures.
    const FVector Center = GestureData ? GestureData->IdlePoseCenterCm : InteractionData->ClosedPoseCenterCm;
    const FVector Extent = GestureData ? GestureData->IdlePoseExtentCm : InteractionData->ClosedPoseExtentCm;
    const FBox Box(Center - Extent, Center + Extent);
    const FVector Closest = Box.GetClosestPointTo(GetActorTransform().InverseTransformPosition(Position));
    return FVector::DistSquared(Position, GetActorTransform().TransformPosition(Closest));
}

bool AReaBullyWorldProp::IsNearestInteraction(APlayerController* Controller) const
{
    APawn* Pawn = Controller ? Controller->GetPawn() : nullptr;
    if (!Pawn || !bInteractionConfigured || !bExplorationInteractionEnabled || IsHidden() ||
        !GetLevel() || !GetLevel()->bIsVisible || !IsWorldReadyForInteraction()) return false;
    const FVector Position = Pawn->GetActorLocation();
    const float Distance = InteractionDistanceSquared(Position);
    if (Distance > FMath::Square(InteractionDistanceCm)) return false;
    for (TActorIterator<AReaBullyWorldProp> It(GetWorld()); It; ++It)
    {
        const AReaBullyWorldProp* Other = *It;
        if (Other == this || !Other->bInteractionConfigured || !Other->bExplorationInteractionEnabled ||
            Other->IsHidden() || !Other->GetLevel() || !Other->GetLevel()->bIsVisible) continue;
        const float OtherDistance = Other->InteractionDistanceSquared(Position);
        if (OtherDistance > FMath::Square(Other->InteractionDistanceCm)) continue;
        if (OtherDistance < Distance - 0.01f ||
            (FMath::Abs(OtherDistance - Distance) <= 0.01f && Other->InteractionSourceId < InteractionSourceId)) return false;
    }
    return true;
}

void AReaBullyWorldProp::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bInteractionConfigured) return;
    if (bInteractionMoving && FMath::IsFinite(DeltaSeconds) && DeltaSeconds > 0.0f)
    {
        const float End = InteractionClip->GetPlayLength();
        InteractionSeconds = FMath::Min(InteractionSeconds + DeltaSeconds, End);
        SetInteractionPose(InteractionClip, InteractionSeconds);
        if (InteractionSeconds >= End)
        {
            bInteractionMoving = false;
            if (GestureData)
            {
                LastGestureEndpointSeconds = InteractionSeconds;
                ++CompletedGestureCount;
                bInteractionOpen = bInteractionTargetOpen = false;
                InteractionClip = nullptr;
                MeshComponent->SetAnimation(GestureData->IdleClip);
                MeshComponent->SetPosition(0.0f, false);
                MeshComponent->Play(true);
                MeshComponent->TickAnimation(0.0f, false);
                MeshComponent->RefreshBoneTransforms();
            }
            else bInteractionOpen = bInteractionTargetOpen;
        }
    }
    APlayerController* Controller = UGameplayStatics::GetPlayerController(this, 0);
    if (Controller && !Controller->IsMoveInputIgnored() && Controller->WasInputKeyJustPressed(EKeys::F) &&
        !(LastPropInputController == Controller && LastPropInputFrame == GFrameCounter) && IsNearestInteraction(Controller))
    {
        LastPropInputController = Controller;
        LastPropInputFrame = GFrameCounter;
        RequestInteractionToggle();
    }
}

TArray<FString> UReaBullyWorldPropTools::GetMeshBoneNames(USkeletalMesh* Mesh)
{
    TArray<FString> Names;
    if (Mesh)
        for (const FMeshBoneInfo& Bone : Mesh->GetRefSkeleton().GetRawRefBoneInfo())
            Names.Add(Bone.Name.ToString());
    return Names;
}

TArray<int32> UReaBullyWorldPropTools::GetMeshBoneParents(USkeletalMesh* Mesh)
{
    TArray<int32> Parents;
    if (Mesh)
        for (const FMeshBoneInfo& Bone : Mesh->GetRefSkeleton().GetRawRefBoneInfo())
            Parents.Add(Bone.ParentIndex);
    return Parents;
}

FString UReaBullyWorldPropTools::ReadCollisionTriangleGeometry(UStaticMesh* Mesh)
{
    auto Result = MakeShared<FJsonObject>();
    FTriMeshCollisionData Data;
    const bool Valid = Mesh && Mesh->GetPhysicsTriMeshData(&Data, true);
    Result->SetBoolField(TEXT("available"), Valid);
    Result->SetStringField(TEXT("mesh"), GetPathNameSafe(Mesh));
    Result->SetBoolField(TEXT("flip_normals"), Data.bFlipNormals);
    TArray<TSharedPtr<FJsonValue>> Positions, Triangles;
    if (Valid)
    {
        for (const auto& Vertex : Data.Vertices)
        {
            TArray<TSharedPtr<FJsonValue>> XYZ;
            for (double Value : {static_cast<double>(Vertex.X), static_cast<double>(Vertex.Y), static_cast<double>(Vertex.Z)})
                XYZ.Add(MakeShared<FJsonValueNumber>(Value));
            Positions.Add(MakeShared<FJsonValueArray>(XYZ));
        }
        for (const auto& Face : Data.Indices)
        {
            TArray<TSharedPtr<FJsonValue>> Indices;
            for (int32 Index : {Face.v0, Face.v1, Face.v2}) Indices.Add(MakeShared<FJsonValueNumber>(Index));
            Triangles.Add(MakeShared<FJsonValueArray>(Indices));
        }
    }
    Result->SetArrayField(TEXT("positions_cm"), Positions);
    Result->SetArrayField(TEXT("triangles"), Triangles);
    FString Json;
    FJsonSerializer::Serialize(Result, TJsonWriterFactory<>::Create(&Json));
    return Json;
}

FString UReaBullyWorldPropTools::InspectCollisionMesh(UStaticMesh* Mesh)
{
    auto Result = MakeShared<FJsonObject>();
    Result->SetStringField(TEXT("status"), TEXT("asset_collision_inspection_only"));
    Result->SetBoolField(TEXT("physics_cook_requested"), false);
    Result->SetBoolField(TEXT("world_physics_scene_validated"), false);
    Result->SetStringField(TEXT("mesh"), GetPathNameSafe(Mesh));
    if (Mesh)
    {
        for (bool AllTriangles : {false, true})
        {
            auto Data = MakeShared<FJsonObject>();
            Data->SetBoolField(TEXT("contains_physics_tri_mesh_data"), Mesh->ContainsPhysicsTriMeshData(AllTriangles));
            FTriMeshCollisionData Triangles;
            Data->SetBoolField(TEXT("get_physics_tri_mesh_data"), Mesh->GetPhysicsTriMeshData(&Triangles, AllTriangles));
            Data->SetNumberField(TEXT("vertices"), Triangles.Vertices.Num());
            Data->SetNumberField(TEXT("triangles"), Triangles.Indices.Num());
            Data->SetNumberField(TEXT("material_indices"), Triangles.MaterialIndices.Num());
            Data->SetBoolField(TEXT("flip_normals"), Triangles.bFlipNormals);
            TArray<TSharedPtr<FJsonValue>> Samples;
            for (int32 Index : {0, 1, 2, 106})
            {
                if (!Triangles.Indices.IsValidIndex(Index)) continue;
                const FTriIndices& Face = Triangles.Indices[Index];
                auto Sample = MakeShared<FJsonObject>();
                Sample->SetNumberField(TEXT("triangle_index"), Index);
                TArray<TSharedPtr<FJsonValue>> Indices, Positions;
                bool Valid = true;
                for (int32 VertexIndex : {Face.v0, Face.v1, Face.v2})
                {
                    Indices.Add(MakeShared<FJsonValueNumber>(VertexIndex));
                    Valid &= Triangles.Vertices.IsValidIndex(VertexIndex);
                    if (Triangles.Vertices.IsValidIndex(VertexIndex))
                    {
                        const FVector3f& V = Triangles.Vertices[VertexIndex];
                        TArray<TSharedPtr<FJsonValue>> XYZ;
                        for (float Value : {V.X, V.Y, V.Z}) XYZ.Add(MakeShared<FJsonValueNumber>(Value));
                        Positions.Add(MakeShared<FJsonValueArray>(XYZ));
                    }
                }
                Sample->SetArrayField(TEXT("vertex_indices"), Indices);
                Sample->SetArrayField(TEXT("positions_mesh_local_cm"), Positions);
                Sample->SetBoolField(TEXT("indices_valid"), Valid);
                if (Valid)
                {
                    const FVector3f A = Triangles.Vertices[Face.v0];
                    const FVector3f B = Triangles.Vertices[Face.v1];
                    const FVector3f C = Triangles.Vertices[Face.v2];
                    const FVector3f Normal = FVector3f::CrossProduct(B - A, C - A).GetSafeNormal();
                    TArray<TSharedPtr<FJsonValue>> XYZ;
                    for (float Value : {Normal.X, Normal.Y, Normal.Z}) XYZ.Add(MakeShared<FJsonValueNumber>(Value));
                    Sample->SetArrayField(TEXT("winding_normal_before_flip_flag"), XYZ);
                }
                Samples.Add(MakeShared<FJsonValueObject>(Sample));
            }
            Data->SetArrayField(TEXT("sample_triangles"), Samples);
            Result->SetObjectField(AllTriangles ? TEXT("all_triangles") : TEXT("collision_enabled_sections"), Data);
        }
        if (const UBodySetup* Body = Mesh->GetBodySetup())
        {
            auto Data = MakeShared<FJsonObject>();
            Data->SetNumberField(TEXT("collision_trace_flag"), static_cast<int32>(Body->CollisionTraceFlag.GetValue()));
            Data->SetNumberField(TEXT("triangle_mesh_geometries"), Body->TriMeshGeometries.Num());
            Data->SetBoolField(TEXT("never_needs_cooked_collision_data"), Body->bNeverNeedsCookedCollisionData);
            Data->SetBoolField(TEXT("created_physics_meshes"), Body->bCreatedPhysicsMeshes);
            Data->SetBoolField(TEXT("failed_to_create_physics_meshes"), Body->bFailedToCreatePhysicsMeshes);
            Data->SetBoolField(TEXT("double_sided_geometry"), Body->bDoubleSidedGeometry);
            Result->SetObjectField(TEXT("body_setup"), Data);
        }
        if (const FStaticMeshRenderData* Render = Mesh->GetRenderData())
        {
            Result->SetNumberField(TEXT("render_lod_count"), Render->LODResources.Num());
            if (Render->LODResources.Num() > 0)
            {
                const FStaticMeshLODResources& LOD = Render->LODResources[0];
                Result->SetNumberField(TEXT("render_lod0_triangles"), LOD.GetNumTriangles());
                TArray<TSharedPtr<FJsonValue>> Sections;
                for (int32 Index = 0; Index < LOD.Sections.Num(); ++Index)
                {
                    const FStaticMeshSection& Section = LOD.Sections[Index];
                    auto Data = MakeShared<FJsonObject>();
                    Data->SetNumberField(TEXT("section"), Index);
                    Data->SetNumberField(TEXT("triangles"), Section.NumTriangles);
                    Data->SetBoolField(TEXT("render_section_collision"), Section.bEnableCollision);
#if WITH_EDITORONLY_DATA
                    Data->SetBoolField(TEXT("section_info_collision"), Mesh->GetSectionInfoMap().Get(0, Index).bEnableCollision);
#endif
                    Sections.Add(MakeShared<FJsonValueObject>(Data));
                }
                Result->SetArrayField(TEXT("render_lod0_sections"), Sections);
            }
        }
    }
    FString Json;
    FJsonSerializer::Serialize(Result, TJsonWriterFactory<>::Create(&Json));
    return Json;
}
