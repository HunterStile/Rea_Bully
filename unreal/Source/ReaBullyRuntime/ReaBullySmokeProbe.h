#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ReaBullySmokeProbe.generated.h"

class AReaBullyExplorer;
class AReaBullyPlayerController;

/** Development-only exercise of real input, CharacterMovement and scene collision. */
UCLASS(NotBlueprintable)
class AReaBullySmokeProbe : public AActor
{
    GENERATED_BODY()
public:
    AReaBullySmokeProbe();
    FString ReportPath;
    virtual void Tick(float DeltaSeconds) override;
private:
    int32 Phase = 0;
    float PhaseTime = 0.0f;
    float TotalTime = 0.0f;
    FVector StartPosition = FVector::ZeroVector;
    FVector WallPosition = FVector::ZeroVector;
    float JumpBase = 0.0f;
    float JumpMaximum = 0.0f;
    float SprintMaximum = 0.0f;
    TArray<FTransform> IdlePose;
    bool bReturnedToThirdPerson = false;
    bool bMovementScreenshot = false;
    bool bJumpWindup = false;
    bool bJumpAirborne = false;
    bool bJumpLanding = false;
    bool bRunningJumpRequested = false;
    bool bRunningJumpAnimated = false;
    bool bStandingScreenshot = false;
    bool bRunningScreenshot = false;
    bool bFallScreenshot = false;
    bool bLandingScreenshot = false;
    bool bObservedFall = false;
    bool bObservedFlail = false;
    bool bObservedFallLand = false;
    TWeakObjectPtr<AReaBullyExplorer> Player;
    TWeakObjectPtr<AReaBullyPlayerController> Controller;
    TWeakObjectPtr<AActor> Wall;
    TSharedPtr<class FJsonObject> Results;
    void Key(const FKey& Input, EInputEvent Event);
    void NextPhase();
    void Capture(const FString& Suffix);
    void Finish(const FString& Error = TEXT(""));
};
