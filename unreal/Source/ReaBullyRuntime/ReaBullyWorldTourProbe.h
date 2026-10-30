#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ReaBullyWorldTourProbe.generated.h"

/** Command-line-only traversal of source-authored positions across recovered areas. */
UCLASS(NotBlueprintable)
class AReaBullyWorldTourProbe : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyWorldTourProbe();
    FString ReportPath;
    FString PlanPath;
    virtual void Tick(float DeltaSeconds) override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
private:
    TArray<TSharedPtr<class FJsonValue>> Checkpoints;
    TArray<TSharedPtr<class FJsonValue>> Records;
    TSharedPtr<class FJsonObject> Current;
    int32 Index = 0;
    int32 Phase = 0;
    bool bLoaded = false;
    double Deadline = 0;
    float WalkTime = 0;
    FVector WalkStart;
    TWeakObjectPtr<class AReaBullyExplorer> WalkPlayer;
    FString CaptureDirectory;
    FString CapturePath;
    FDelegateHandle CaptureDelegate;
    bool bCaptureProcessed = false;
    float CaptureSettleTime = 0;
    int32 CaptureSettleFrames = 0;
    int32 Captured = 0;
    struct FAmbientSample
    {
        TWeakObjectPtr<class AReaBullyAmbientPed> Ped;
        TArray<FTransform> LocalBones;
        FVector Position;
    };
    TArray<FAmbientSample> AmbientSamples;
    void BeginAmbientSamples();
    void RecordAmbientSamples();
    void WalkKey(bool Pressed);
    void ScreenshotProcessed();
    void CompleteCheckpoint();
    void RecordFailure(const FString& Reason);
    void Finish(const FString& Error = TEXT(""));
};
