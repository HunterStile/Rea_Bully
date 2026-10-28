#include "ReaBullyWorldDoor.h"
#include "ReaBullyWorld.h"
#include "Animation/AnimSequence.h"
#include "Components/BoxComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/Level.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/Character.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/PlayerInput.h"
#include "Dom/JsonObject.h"
#include "InputCoreTypes.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace
{
// This exploration runtime uses local player 0. A single press must not reach
// a second actor after RequestTravel moves that player in the same frame.
TWeakObjectPtr<APlayerController> LastDoorInputController;
uint64 LastDoorInputFrame = MAX_uint64;
}

AReaBullyWorldDoor::AReaBullyWorldDoor()
{
    PrimaryActorTick.bCanEverTick = true;
    MeshComponent = CreateDefaultSubobject<USkeletalMeshComponent>(TEXT("OriginalDoorSkin"));
    RootComponent = MeshComponent;
    MeshComponent->SetMobility(EComponentMobility::Movable);
    MeshComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    MeshComponent->SetGenerateOverlapEvents(false);
    ClosedBlocker = CreateDefaultSubobject<UBoxComponent>(TEXT("ReconstructedClosedDoorBlocker"));
    ClosedBlocker->SetupAttachment(RootComponent);
    ClosedBlocker->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    ClosedBlocker->SetCollisionObjectType(ECC_WorldDynamic);
    ClosedBlocker->SetCollisionResponseToAllChannels(ECR_Ignore);
    ClosedBlocker->SetCollisionResponseToChannel(ECC_Pawn, ECR_Block);
    ClosedBlocker->SetGenerateOverlapEvents(false);
}

bool AReaBullyWorldDoor::ConfigureRecoveredDoor(UReaBullyWorldDoorData* Source)
{
    bConfigured = false;
    if (!Source || !Source->Mesh || !Source->Mesh->GetSkeleton() || Source->SourceSHA256.Len() != 64 ||
        !Source->OpenClip || !Source->CloseClip || !Source->ClosedClip ||
        Source->OpenClip->GetSkeleton() != Source->Mesh->GetSkeleton() ||
        Source->CloseClip->GetSkeleton() != Source->Mesh->GetSkeleton() ||
        Source->ClosedClip->GetSkeleton() != Source->Mesh->GetSkeleton() ||
        !Source->OpenOriginal.EndsWith(TEXT("\\OPEN")) ||
        !Source->CloseOriginal.EndsWith(TEXT("\\CLOSE")) ||
        !Source->ClosedOriginal.EndsWith(TEXT("\\IDLE")) ||
        Source->ClosedPoseSourceSHA256 != Source->SourceSHA256 ||
        Source->ClosedPoseOriginalClip != Source->ClosedOriginal ||
        Source->ClosedPoseCenterCm.ContainsNaN() || Source->ClosedPoseExtentCm.ContainsNaN() ||
        Source->ClosedPoseExtentCm.X <= 0 || Source->ClosedPoseExtentCm.Y <= 0 || Source->ClosedPoseExtentCm.Z <= 0)
    {
        ClosedBlocker->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        SetActorHiddenInGame(true);
        return false;
    }
    Data = Source;
    MeshComponent->SetSkeletalMeshAsset(Data->Mesh);
    MeshComponent->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    MeshComponent->SetSimulatePhysics(false);
    MeshComponent->SetAnimationMode(EAnimationMode::AnimationSingleNode);
    MeshComponent->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
    ActiveClip = nullptr;
    SetPose(Data->ClosedClip, 0.0f);
    // Mesh asset bounds describe raw bind vertices, which need not match IDLE:
    // ESCDoorL/R's bind boxes are flat and would select the adjacent leaf.
    // This source-pose AABB remains an exploration proxy, not recovered COL.
    ClosedBlocker->SetRelativeLocation(Data->ClosedPoseCenterCm);
    ClosedBlocker->SetBoxExtent(Data->ClosedPoseExtentCm.ComponentMax(FVector(2.0f)), false);
    bOpen = bAnimating = bTargetOpen = false;
    ToggleCount = SuccessfulTravels = 0;
    bConfigured = true;
    SetActorHiddenInGame(false);
    UpdateClosedCollision();
    return true;
}

void AReaBullyWorldDoor::SetPose(UAnimSequence* Clip, float Seconds)
{
    if (ActiveClip != Clip)
    {
        ActiveClip = Clip;
        MeshComponent->SetAnimation(Clip);
    }
    MeshComponent->SetPosition(Seconds, false);
    MeshComponent->Stop();
    MeshComponent->TickAnimation(0.0f, false);
    MeshComponent->RefreshBoneTransforms();
}

bool AReaBullyWorldDoor::DoorwayOccupied() const
{
    if (!GetWorld()) return false;
    FCollisionQueryParams Query(SCENE_QUERY_STAT(RecoveredDoorClearance), false, this);
    return GetWorld()->OverlapAnyTestByObjectType(ClosedBlocker->GetComponentLocation(),
        ClosedBlocker->GetComponentQuat(), FCollisionObjectQueryParams(ECC_Pawn),
        FCollisionShape::MakeBox(ClosedBlocker->GetScaledBoxExtent()), Query);
}

void AReaBullyWorldDoor::UpdateClosedCollision()
{
    const bool Block = bConfigured && bReconstructedClosedCollision && !bOpen && !bAnimating && !DoorwayOccupied();
    ClosedBlocker->SetCollisionEnabled(Block ? ECollisionEnabled::QueryOnly : ECollisionEnabled::NoCollision);
}

bool AReaBullyWorldDoor::RequestToggle()
{
    if (!bConfigured || bAnimating || !bExplorationUnlocked || !IsWorldReadyForInteraction()) return false;
    if (bOpen && bHasTravelTarget && TryTravel()) return true;
    if (bOpen && DoorwayOccupied()) return false;
    bTargetOpen = !bOpen;
    bAnimating = true;
    ClipSeconds = 0.0f;
    ClosedBlocker->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    SetPose(bTargetOpen ? Data->OpenClip : Data->CloseClip, 0.0f);
    ++ToggleCount;
    return true;
}

bool AReaBullyWorldDoor::IsWorldReadyForInteraction() const
{
    if (!GetWorld()) return false;
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It)
        if (!It->IsReady()) return false;
    return true;
}

bool AReaBullyWorldDoor::TryTravel()
{
    if (!bHasTravelTarget || TravelArea.IsNone()) return false;
    APlayerController* Controller = UGameplayStatics::GetPlayerController(this, 0);
    ACharacter* Player = Controller ? Cast<ACharacter>(Controller->GetPawn()) : nullptr;
    if (!Player) return false;
    const FVector Center = TravelDestinationCm + FVector(0, 0, Player->GetCapsuleComponent()->GetScaledCapsuleHalfHeight() + 2.0f);
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It)
    {
        if (!It->IsReady() || !It->RequestTravel(Center, TravelArea)) continue;
        ++SuccessfulTravels;
        const FRotator Facing(0.0f, TravelYawDegrees, 0.0f);
        Player->SetActorRotation(Facing);
        Controller->SetControlRotation(Facing);
        return true;
    }
    UE_LOG(LogTemp, Warning, TEXT("Door destination area is unavailable: %s -> %s"), *SourceName, *TravelArea.ToString());
    return false;
}

bool AReaBullyWorldDoor::IsNearestDoor(APlayerController* Controller) const
{
    APawn* Pawn = Controller ? Controller->GetPawn() : nullptr;
    if (!Pawn || !bConfigured || IsHidden() || !GetLevel()->bIsVisible || !IsWorldReadyForInteraction()) return false;
    const FVector Position = Pawn->GetActorLocation();
    const float Distance = FVector::DistSquared(Position, ClosedBlocker->GetComponentLocation());
    if (Distance > FMath::Square(InteractionDistanceCm)) return false;
    for (TActorIterator<AReaBullyWorldDoor> It(GetWorld()); It; ++It)
    {
        const AReaBullyWorldDoor* Other = *It;
        if (Other == this || !Other->bConfigured || Other->IsHidden() ||
            !Other->GetLevel()->bIsVisible || !Other->bExplorationUnlocked) continue;
        const float OtherDistance = FVector::DistSquared(Position, Other->ClosedBlocker->GetComponentLocation());
        if (OtherDistance > FMath::Square(Other->InteractionDistanceCm)) continue;
        if (OtherDistance < Distance - 0.01f ||
            (FMath::Abs(OtherDistance - Distance) <= 0.01f && Other->SourceReference < SourceReference)) return false;
    }
    return true;
}

FString AReaBullyWorldDoor::GetInteractionDiagnostics(APlayerController* Controller) const
{
    auto Result = MakeShared<FJsonObject>();
    Result->SetNumberField(TEXT("frame"), static_cast<double>(GFrameCounter));
    Result->SetStringField(TEXT("source_name"), SourceName);
    Result->SetStringField(TEXT("model_key"), Data ? Data->ModelKey : TEXT(""));
    Result->SetBoolField(TEXT("configured"), bConfigured);
    Result->SetBoolField(TEXT("hidden"), IsHidden());
    Result->SetBoolField(TEXT("level_visible"), GetLevel() && GetLevel()->bIsVisible);
    Result->SetBoolField(TEXT("world_ready"), IsWorldReadyForInteraction());
    Result->SetBoolField(TEXT("exploration_unlocked"), bExplorationUnlocked);
    Result->SetBoolField(TEXT("animating"), bAnimating);
    Result->SetBoolField(TEXT("open"), bOpen);
    Result->SetNumberField(TEXT("toggle_count"), ToggleCount);
    Result->SetNumberField(TEXT("accepted_travels"), SuccessfulTravels);
    Result->SetNumberField(TEXT("interaction_distance_cm"), InteractionDistanceCm);
    Result->SetStringField(TEXT("box_center_cm"), ClosedBlocker->GetComponentLocation().ToString());
    Result->SetStringField(TEXT("box_extent_cm"), ClosedBlocker->GetScaledBoxExtent().ToString());
    Result->SetStringField(TEXT("box_rotation"), ClosedBlocker->GetComponentQuat().ToString());
    Result->SetStringField(TEXT("bounds_source_sha256"), Data ? Data->ClosedPoseSourceSHA256 : TEXT(""));
    Result->SetStringField(TEXT("bounds_original_clip"), Data ? Data->ClosedPoseOriginalClip : TEXT(""));
    Result->SetBoolField(TEXT("nearest_selected"), IsNearestDoor(Controller));
    const APawn* Pawn = Controller ? Controller->GetPawn() : nullptr;
    Result->SetStringField(TEXT("pawn"), GetNameSafe(Pawn));
    if (Pawn)
    {
        Result->SetStringField(TEXT("pawn_position_cm"), Pawn->GetActorLocation().ToString());
        Result->SetStringField(TEXT("pawn_velocity_cm_s"), Pawn->GetVelocity().ToString());
        Result->SetNumberField(TEXT("distance_cm"), FVector::Dist(Pawn->GetActorLocation(), ClosedBlocker->GetComponentLocation()));
    }
    if (Controller)
    {
        Result->SetBoolField(TEXT("just_pressed"), Controller->WasInputKeyJustPressed(EKeys::E));
        Result->SetBoolField(TEXT("key_down"), Controller->IsInputKeyDown(EKeys::E));
        Result->SetBoolField(TEXT("ignore_move"), Controller->IsMoveInputIgnored());
        Result->SetBoolField(TEXT("ignore_look"), Controller->IsLookInputIgnored());
        Result->SetBoolField(TEXT("door_consumed_this_frame"),
            LastDoorInputController.Get() == Controller && LastDoorInputFrame == GFrameCounter);
        Result->SetStringField(TEXT("player_input_class"), Controller->PlayerInput ? Controller->PlayerInput->GetClass()->GetName() : TEXT("None"));
        const FKeyState* KeyState = Controller->PlayerInput ? Controller->PlayerInput->GetKeyState(EKeys::E) : nullptr;
        Result->SetBoolField(TEXT("has_key_state"), KeyState != nullptr);
        if (KeyState)
        {
            Result->SetNumberField(TEXT("pending_pressed"), KeyState->EventAccumulator[IE_Pressed].Num());
            Result->SetNumberField(TEXT("processed_pressed"), KeyState->EventCounts[IE_Pressed].Num());
            Result->SetNumberField(TEXT("pending_released"), KeyState->EventAccumulator[IE_Released].Num());
            Result->SetNumberField(TEXT("processed_released"), KeyState->EventCounts[IE_Released].Num());
            Result->SetBoolField(TEXT("key_consumed"), KeyState->bConsumed);
        }
    }
    TArray<TSharedPtr<FJsonValue>> Candidates;
    if (Pawn)
        for (TActorIterator<AReaBullyWorldDoor> It(GetWorld()); It; ++It)
        {
            const double Distance = FVector::Dist(Pawn->GetActorLocation(), It->ClosedBlocker->GetComponentLocation());
            if (*It != this && Distance > 500.0) continue;
            auto Entry = MakeShared<FJsonObject>();
            Entry->SetStringField(TEXT("name"), It->SourceName);
            Entry->SetNumberField(TEXT("distance_cm"), Distance);
            Entry->SetStringField(TEXT("box_center_cm"), It->ClosedBlocker->GetComponentLocation().ToString());
            Entry->SetBoolField(TEXT("configured"), It->bConfigured);
            Entry->SetBoolField(TEXT("visible"), !It->IsHidden() && It->GetLevel()->bIsVisible);
            Entry->SetBoolField(TEXT("unlocked"), It->bExplorationUnlocked);
            Entry->SetBoolField(TEXT("in_range"), Distance <= It->InteractionDistanceCm);
            Entry->SetBoolField(TEXT("selected"), It->IsNearestDoor(Controller));
            Candidates.Add(MakeShared<FJsonValueObject>(Entry));
        }
    Result->SetArrayField(TEXT("nearby_candidates"), Candidates);
    FString Json;
    FJsonSerializer::Serialize(Result, TJsonWriterFactory<>::Create(&Json));
    return Json;
}

void AReaBullyWorldDoor::OnConstruction(const FTransform& Transform)
{
    Super::OnConstruction(Transform);
    if (Data) ConfigureRecoveredDoor(Data);
}

void AReaBullyWorldDoor::BeginPlay()
{
    Super::BeginPlay();
    if (!ConfigureRecoveredDoor(Data))
        UE_LOG(LogTemp, Error, TEXT("Recovered door has missing or mismatched original assets: %s"), *SourceName);
}

void AReaBullyWorldDoor::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bConfigured || !GetLevel()->bIsVisible) return;
    if (bAnimating)
    {
        ClipSeconds = FMath::Min(ClipSeconds + DeltaSeconds, ActiveClip->GetPlayLength());
        SetPose(ActiveClip, ClipSeconds);
        if (ClipSeconds >= ActiveClip->GetPlayLength())
        {
            bOpen = bTargetOpen;
            bAnimating = false;
            // Keep the exact last sample; never snap back to the reference pose.
            if (bOpen && bHasTravelTarget) TryTravel();
        }
    }
    UpdateClosedCollision();
    APlayerController* Controller = UGameplayStatics::GetPlayerController(this, 0);
    // Temporary event-boundary diagnostic; never emits once per ordinary tick.
    if (Controller && Controller->WasInputKeyJustPressed(EKeys::E) &&
        FParse::Param(FCommandLine::Get(), TEXT("ReaBullyDoorDiagnostics")) &&
        (SourceName == TEXT("DT_tschool_SchoolFrontDoorL") || IsNearestDoor(Controller)))
        UE_LOG(LogTemp, Display, TEXT("[DEBUG-DOOR-V06] %s"), *GetInteractionDiagnostics(Controller));
    if (Controller && bExplorationUnlocked && IsNearestDoor(Controller))
    {
        if (GEngine)
            GEngine->AddOnScreenDebugMessage(0x52454144, 0.12f, FColor::White,
                bHasTravelTarget ? TEXT("E  Entra") : (bOpen ? TEXT("E  Chiudi porta") : TEXT("E  Apri porta")));
        if (Controller->WasInputKeyJustPressed(EKeys::E) &&
            (LastDoorInputController.Get() != Controller || LastDoorInputFrame != GFrameCounter))
        {
            LastDoorInputController = Controller;
            LastDoorInputFrame = GFrameCounter;
            RequestToggle();
        }
    }
}
