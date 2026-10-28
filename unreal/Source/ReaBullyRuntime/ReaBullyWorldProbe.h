#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ReaBullyWorldProbe.generated.h"

/** Explicit command-line-only check of the streamable reconstruction. */
UCLASS(NotBlueprintable)
class AReaBullyWorldProbe : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyWorldProbe();
    FString ReportPath;
    virtual void Tick(float DeltaSeconds) override;
private:
    int32 Phase = 0;
    float PhaseTime = 0.0f;
    double Started = 0.0;
    FVector StartPosition;
    TWeakObjectPtr<APawn> OriginalPlayer;
    TWeakObjectPtr<class AReaBullyWorldDoor> TestedDoor;
    TArray<FTransform> ClosedDoorBones;
    FName ExpectedTravelArea;
    bool bDoorKeyHeld = false;
    bool bWalkStarted = false;
    bool bWindingProbeApplied = false;
    TSharedPtr<class FJsonObject> Results;
    void Key(bool Pressed);
    void DoorKey(bool Pressed);
    void RecordPlayerState(const FString& Label, class AReaBullyExplorer* Player);
    void Finish(const FString& Error = TEXT(""));
};
