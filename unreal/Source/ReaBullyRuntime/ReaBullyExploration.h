#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "GameFramework/GameModeBase.h"
#include "GameFramework/HUD.h"
#include "GameFramework/PlayerController.h"
#include "ReaBullyPlayerActions.h"
#include "ReaBullyExploration.generated.h"

class UCameraComponent;
class USpringArmComponent;
class UBlendSpace1D;
class UAnimSequence;

enum class EReaBullyActionPhase : uint8 { Locomotion, Windup, Airborne, Landing, Falling };

/** Exploration of recovered geometry with the original skinned player. */
UCLASS()
class REABULLYRUNTIME_API AReaBullyExplorer : public ACharacter
{
    GENERATED_BODY()
public:
    AReaBullyExplorer();
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;
    virtual void FellOutOfWorld(const UDamageType& DamageType) override;
    virtual void OnJumped_Implementation() override;
    virtual void OnMovementModeChanged(EMovementMode PreviousMovementMode, uint8 PreviousCustomMode = 0) override;
    virtual void Landed(const FHitResult& Hit) override;
    bool IsFirstPerson() const { return bFirstPerson; }
    EReaBullyActionPhase GetActionPhase() const { return ActionPhase; }
    int32 GetCompletedJumpCount() const { return CompletedJumpCount; }

private:
    UPROPERTY(VisibleAnywhere)
    TObjectPtr<UCameraComponent> Camera;
    UPROPERTY(VisibleAnywhere)
    TObjectPtr<USpringArmComponent> CameraArm;
    UPROPERTY()
    TObjectPtr<UBlendSpace1D> Locomotion;
    UPROPERTY()
    TObjectPtr<UReaBullyPlayerActions> Actions;
    FReaBullyJumpProfile ActiveJump;
    EReaBullyActionPhase ActionPhase = EReaBullyActionPhase::Locomotion;
    float PhaseSeconds = 0.0f;
    float EstimatedFlightSeconds = 0.0f;
    int32 CompletedJumpCount = 0;
    bool bJumpLaunchPending = false;
    bool bLandingFromJump = false;
    bool bSprintRequested = false;
    void RequestJump();
    void UpdateAction(float DeltaSeconds);
    void SetActionPose(UAnimSequence* Clip, float SampleTime, float DeltaSeconds);
    bool bFirstPerson = false;
    void ToggleView();
    void ApplyView();
    void MoveForward(float Value);
    void MoveRight(float Value);
    void StartSprint();
    void StopSprint();
};

UCLASS()
class REABULLYRUNTIME_API AReaBullyPlayerController : public APlayerController
{
    GENERATED_BODY()
public:
    virtual void BeginPlay() override;
    virtual void SetupInputComponent() override;
    void ReturnToStart();

private:
    void ExitExploration();
};

UCLASS()
class REABULLYRUNTIME_API AReaBullyExplorationHUD : public AHUD
{
    GENERATED_BODY()
public:
    virtual void DrawHUD() override;
};

UCLASS()
class REABULLYRUNTIME_API AReaBullyGameMode : public AGameModeBase
{
    GENERATED_BODY()
public:
    AReaBullyGameMode();
    virtual void StartPlay() override;
};
