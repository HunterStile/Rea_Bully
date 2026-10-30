#include "ReaBullyWorld.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/SceneComponent.h"
#include "Engine/LevelStreamingDynamic.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/PlayerController.h"

AReaBullyZoneGeometry::AReaBullyZoneGeometry()
{
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("ZoneOrigin"));
    RootComponent->SetMobility(EComponentMobility::Static);
}

int32 AReaBullyZoneGeometry::AddOriginalInstances(UStaticMesh* Mesh, const TArray<FTransform>& Transforms,
    bool VisualOnly, bool ReverseCulling)
{
    if (!Mesh || Transforms.IsEmpty()) return 0;
    for (const FTransform& Transform : Transforms)
        if (Transform.ContainsNaN() || !Transform.IsRotationNormalized()) return 0;
    Modify();
    UHierarchicalInstancedStaticMeshComponent* Component = NewObject<UHierarchicalInstancedStaticMeshComponent>(
        this, MakeUniqueObjectName(this, UHierarchicalInstancedStaticMeshComponent::StaticClass(), TEXT("OriginalShape")));
    AddInstanceComponent(Component);
    Component->SetupAttachment(RootComponent);
    Component->SetMobility(EComponentMobility::Static);
    Component->SetStaticMesh(Mesh);
    Component->SetCollisionProfileName(VisualOnly ? TEXT("NoCollision") : TEXT("BlockAll"));
    Component->SetCastShadow(!VisualOnly);
    Component->bDisallowNanite = true;
    Component->SetReverseCulling(ReverseCulling);
    Component->RegisterComponent();
    Component->AddInstances(Transforms, false, false, true);
    return Component->GetInstanceCount();
}

int32 AReaBullyZoneGeometry::AddOriginalCollisionInstances(UStaticMesh* Mesh, const TArray<FTransform>& Transforms)
{
    if (!Mesh || Transforms.IsEmpty()) return 0;
    for (const FTransform& Transform : Transforms)
        if (Transform.ContainsNaN() || !Transform.IsRotationNormalized()) return 0;
    Modify();
    auto* Component = NewObject<UHierarchicalInstancedStaticMeshComponent>(this,
        MakeUniqueObjectName(this, UHierarchicalInstancedStaticMeshComponent::StaticClass(), TEXT("OriginalCollision")));
    AddInstanceComponent(Component);
    Component->SetupAttachment(RootComponent);
    Component->SetMobility(EComponentMobility::Static);
    Component->SetStaticMesh(Mesh);
    Component->SetCollisionProfileName(TEXT("BlockAll"));
    Component->SetCastShadow(false);
    Component->SetVisibility(false);
    Component->SetHiddenInGame(true);
    Component->bDisallowNanite = true;
    Component->ComponentTags.Add(TEXT("OriginalCOL3"));
    Component->RegisterComponent();
    Component->AddInstances(Transforms, false, false, true);
    return Component->GetInstanceCount();
}

AReaBullyWorldDirector::AReaBullyWorldDirector()
{
    PrimaryActorTick.bCanEverTick = true;
}

void AReaBullyWorldDirector::SetPlayerLocked(bool Locked)
{
    APlayerController* Controller = GetWorld()->GetFirstPlayerController();
    if (!Controller) return;
    const bool Changed = bInputLocked != Locked;
    if (Changed)
    {
        Controller->SetIgnoreMoveInput(Locked);
        Controller->SetIgnoreLookInput(Locked);
    }
    if (ACharacter* Character = Cast<ACharacter>(Controller->GetPawn()))
    {
        if (Locked) Character->GetCharacterMovement()->DisableMovement();
        else if (Changed) Character->GetCharacterMovement()->SetMovementMode(MOVE_Falling);
    }
    bInputLocked = Locked;
}

void AReaBullyWorldDirector::BeginPlay()
{
    Super::BeginPlay();
    if (!Data || Data->Zones.IsEmpty())
    {
        UE_LOG(LogTemp, Error, TEXT("World director has no verified zone manifest"));
        SetPlayerLocked(true);
        return;
    }
    Streams.SetNum(Data->Zones.Num());
    PendingDestination = Data->Start;
    SetPlayerLocked(true);
    UpdateStreaming();
}

bool AReaBullyWorldDirector::RequestTravel(FVector Destination, FName Area)
{
    if (!Data || Destination.ContainsNaN()) return false;
    bool Available = false;
    for (const FReaBullyWorldZone& Zone : Data->Zones) Available |= Zone.Area == Area;
    if (!Available) return false;
    ActiveArea = Area;
    PendingDestination = Destination;
    bReady = false;
    SetPlayerLocked(true);
    if (APlayerController* Controller = GetWorld()->GetFirstPlayerController())
        if (APawn* Player = Controller->GetPawn()) Player->SetActorLocation(Destination);
    UpdateStreaming();
    return true;
}

int32 AReaBullyWorldDirector::VisibleZones() const
{
    int32 Count = 0;
    for (const auto& Stream : Streams) if (Stream && Stream->IsLevelVisible()) ++Count;
    return Count;
}

TArray<FName> AReaBullyWorldDirector::VisibleZoneNames() const
{
    TArray<FName> Names;
    if (!Data) return Names;
    for (int32 Index = 0; Index < Streams.Num(); ++Index)
        if (Streams[Index] && Streams[Index]->IsLevelVisible() && Data->Zones.IsValidIndex(Index))
            Names.Add(Data->Zones[Index].Name);
    return Names;
}

void AReaBullyWorldDirector::UpdateStreaming()
{
    if (!Data) return;
    SetPlayerLocked(!bReady);
    APlayerController* Controller = GetWorld()->GetFirstPlayerController();
    APawn* Player = Controller ? Controller->GetPawn() : nullptr;
    const FVector Position = bReady && Player ? Player->GetActorLocation() : PendingDestination;
    bool RequiredVisible = true;
    int32 Required = 0;
    for (int32 Index = 0; Index < Data->Zones.Num(); ++Index)
    {
        const FReaBullyWorldZone& Zone = Data->Zones[Index];
        const FBox Bounds(Zone.Minimum, Zone.Maximum);
        const FVector Closest = Bounds.GetClosestPointTo(Position);
        const double Distance = FVector::Dist2D(Position, Closest);
        const bool Wanted = Zone.Area == ActiveArea && (!Zone.Exterior || Zone.AlwaysLoaded ||
            Distance < (Streams[Index] && Streams[Index]->ShouldBeLoaded() ? Data->UnloadDistance : Data->LoadDistance));
        if (Wanted)
        {
            ++Required;
            if (!Streams[Index])
            {
                bool Success = false;
                Streams[Index] = ULevelStreamingDynamic::LoadLevelInstanceBySoftObjectPtr(this, Zone.Level,
                    FVector::ZeroVector, FRotator::ZeroRotator, Success, TEXT("REA_") + Zone.Name.ToString());
                if (!Success) UE_LOG(LogTemp, Error, TEXT("Could not request original zone %s"), *Zone.Name.ToString());
            }
            if (Streams[Index])
            {
                Streams[Index]->SetShouldBeLoaded(true);
                Streams[Index]->SetShouldBeVisible(true);
            }
            RequiredVisible &= Streams[Index] && Streams[Index]->IsLevelVisible();
        }
        else if (Streams[Index])
        {
            Streams[Index]->SetShouldBeVisible(false);
            Streams[Index]->SetShouldBeLoaded(false);
            // Original areas can overlap in coordinates. Do not find an arrival
            // floor in the outgoing area while its level is still visible.
            RequiredVisible &= !Streams[Index]->IsLevelVisible();
        }
    }
    if (!bReady && Required > 0 && RequiredVisible && Player)
    {
        FHitResult Floor;
        FCollisionQueryParams Params(SCENE_QUERY_STAT(WorldStartFloor), false, Player);
        const bool Grounded = GetWorld()->LineTraceSingleByChannel(Floor, PendingDestination + FVector(0, 0, 150),
            PendingDestination - FVector(0, 0, 300), ECC_Visibility, Params) && Floor.ImpactNormal.Z > 0.7;
        if (Grounded)
        {
            if (ACharacter* Character = Cast<ACharacter>(Player))
            {
                const float HalfHeight = Character->GetCapsuleComponent()->GetScaledCapsuleHalfHeight();
                Character->SetActorLocation(Floor.ImpactPoint + FVector(0, 0, HalfHeight + 2.0f),
                    false, nullptr, ETeleportType::TeleportPhysics);
            }
            bReady = true;
            SetPlayerLocked(false);
        }
    }
}

void AReaBullyWorldDirector::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    PollTime += DeltaSeconds;
    if (PollTime < 0.5f) return;
    PollTime = 0.0f;
    UpdateStreaming();
}
