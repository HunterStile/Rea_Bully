#include "ReaBullyAmbient.h"
#include "ReaBullyWorld.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Animation/BlendSpace1D.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/CharacterMovementComponent.h"

AReaBullyAmbientPed::AReaBullyAmbientPed()
{
    PrimaryActorTick.bCanEverTick = true;
    GetCapsuleComponent()->InitCapsuleSize(33.0f, 96.0f);
    bUseControllerRotationYaw = false;
    GetCharacterMovement()->bRunPhysicsWithNoController = true;
    GetCharacterMovement()->bOrientRotationToMovement = true;
    GetCharacterMovement()->RotationRate = FRotator(0.0f, 360.0f, 0.0f);
    GetCharacterMovement()->MaxStepHeight = 40.0f;
    GetCharacterMovement()->GravityScale = 1.4f;
}

void AReaBullyAmbientPed::SetLocomotion()
{
    if (!Data || !Data->Locomotion) return;
    GetMesh()->SetAnimation(Data->Locomotion);
    GetMesh()->Play(true);
}

void AReaBullyAmbientPed::BeginPlay()
{
    Super::BeginPlay();
    GetCharacterMovement()->DisableMovement();
    if (!Data || !Data->Mesh || !Data->Locomotion || Data->HalfHeight < 40.0f || Data->WalkSpeed <= 0.0f)
    {
        UE_LOG(LogTemp, Error, TEXT("Ambient ped lacks its own verified source assets: %s"), *GetName());
        SetActorHiddenInGame(true);
        return;
    }
    GetCapsuleComponent()->SetCapsuleHalfHeight(Data->HalfHeight);
    GetCharacterMovement()->MaxWalkSpeed = Data->WalkSpeed;
    GetMesh()->SetSkeletalMeshAsset(Data->Mesh);
    GetMesh()->SetRelativeLocation(FVector(0, 0, -Data->HalfHeight));
    GetMesh()->SetRelativeRotation(FRotator(0, 90, 0));
    GetMesh()->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    GetMesh()->SetAnimationMode(EAnimationMode::AnimationSingleNode);
    GetMesh()->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
    SetLocomotion();
    SetActorHiddenInGame(true);
    PreviousPosition = GetActorLocation();
}

void AReaBullyAmbientPed::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!Data || !Data->Mesh || !Data->Locomotion) return;
    if (!bPlacedOnOriginalFloor)
    {
        for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It)
            if (!It->IsReady()) return;
        FHitResult Floor;
        FCollisionQueryParams Params(SCENE_QUERY_STAT(AmbientOriginalFloor), false, this);
        // Reconstructed vertical grounding of the original placement. Starting
        // near its feet avoids selecting roofs above the original POI.
        const FVector SourceFeet = GetActorLocation() - FVector(0, 0, Data->HalfHeight + 2.0f);
        if (!GetWorld()->LineTraceSingleByChannel(Floor, SourceFeet + FVector(0, 0, 40),
            SourceFeet - FVector(0, 0, 400), ECC_Visibility, Params) || Floor.ImpactNormal.Z < .7f) return;
        SetActorLocation(Floor.ImpactPoint + FVector(0, 0, Data->HalfHeight + 2.0f));
        SetActorHiddenInGame(false);
        GetCharacterMovement()->SetMovementMode(MOVE_Walking);
        PreviousPosition = GetActorLocation();
        bPlacedOnOriginalFloor = true;
    }
    TotalDistance += FVector::Dist2D(GetActorLocation(), PreviousPosition);
    PreviousPosition = GetActorLocation();
    WaitRemaining = FMath::Max(0.0f, WaitRemaining - DeltaSeconds);
    if (Route.Num() > 1 && WaitRemaining <= 0.0f)
    {
        const FVector Delta = Route[Target].Position - GetActorLocation();
        if (Delta.Size2D() < 40.0f)
        {
            WaitRemaining = FMath::Max(0.0f, Route[Target].WaitSeconds);
            if (Target + Direction >= Route.Num() || Target + Direction < 0) Direction *= -1;
            Target += Direction;
        }
        else
        {
            // Reject ledges and unloaded ground instead of walking into a void.
            const FVector Ahead = GetActorLocation() + Delta.GetSafeNormal2D() * 70.0f;
            FHitResult Ground;
            FCollisionQueryParams Params(SCENE_QUERY_STAT(AmbientRouteFloor), false, this);
            if (GetWorld()->LineTraceSingleByChannel(Ground, Ahead + FVector(0, 0, 20),
                Ahead - FVector(0, 0, Data->HalfHeight + 100), ECC_Visibility, Params) && Ground.ImpactNormal.Z > .7f)
                AddMovementInput(Delta.GetSafeNormal2D(), 1.0f, true);
        }
    }
    const float Speed = GetVelocity().Size2D();
    IdleSeconds = Speed < 5.0f ? IdleSeconds + DeltaSeconds : 0.0f;
    if (AmbientRemaining > 0.0f)
    {
        AmbientRemaining -= DeltaSeconds;
        if (Speed > 5.0f || AmbientRemaining <= 0.0f)
        {
            AmbientRemaining = 0.0f;
            SetLocomotion();
        }
    }
    else if (IdleSeconds > 12.0f && Data->AmbientIdle)
    {
        // The clip is original; this idle scheduling is a reconstruction.
        GetMesh()->SetAnimation(Data->AmbientIdle);
        GetMesh()->Play(false);
        AmbientRemaining = Data->AmbientIdle->GetPlayLength();
        IdleSeconds = 0.0f;
    }
    if (AmbientRemaining <= 0.0f)
        if (UAnimSingleNodeInstance* Anim = GetMesh()->GetSingleNodeInstance())
            Anim->SetBlendSpacePosition(FVector(Speed, 0, 0));
}
