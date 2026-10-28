#include "ReaBullyWorldProp.h"
#include "Animation/AnimSequence.h"
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

namespace
{
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
}

AReaBullyWorldProp::AReaBullyWorldProp()
{
    PrimaryActorTick.bCanEverTick = false;
    MeshComponent = CreateDefaultSubobject<USkeletalMeshComponent>(TEXT("OriginalObjectSkin"));
    RootComponent = MeshComponent;
    MeshComponent->SetMobility(EComponentMobility::Movable);
    MeshComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    MeshComponent->SetGenerateOverlapEvents(false);
}

bool AReaBullyWorldProp::ConfigureRecoveredProp(UReaBullyWorldPropData* Source)
{
    if (!Source || !Source->Mesh || !Source->Mesh->GetSkeleton() ||
        Source->SourceSHA256.Len() != 64 || !HasApprovedAmbientPolicy(Source))
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
