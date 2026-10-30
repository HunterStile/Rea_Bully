#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ReaBullyPropGestureProbe.generated.h"

/** Real controller-input verification of six source-bound carnival attendants. */
UCLASS(NotBlueprintable)
class AReaBullyPropGestureProbe : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyPropGestureProbe();
    FString ReportPath;
    FString PlanPath;
    virtual void Tick(float DeltaSeconds) override;
private:
    TArray<TSharedPtr<class FJsonValue>> Placements, Records;
    TMap<FString, TSharedPtr<class FJsonObject>> Models;
    TSharedPtr<class FJsonObject> Current;
    TWeakObjectPtr<class AReaBullyWorldProp> Target;
    TWeakObjectPtr<class AReaBullyExplorer> TestedPlayer;
    TMap<TWeakObjectPtr<class AReaBullyWorldProp>, int32> OtherCounts;
    TArray<FTransform> MovingBones;
    FTransform SourceTransform;
    FVector IdleCenter, IdleExtent;
    FString SourceId, ModelKey, SourceDigest;
    int32 Index = 0, Phase = 0, Approach = 0, Attempt = 0;
    float PhaseSeconds = 0, KeySeconds = 0, IdleStartTime = 0;
    double Deadline = 0;
    bool bLoaded = false, bKeyHeld = false, bSawGesture = false, bBonesChanged = false;
    void Key(bool Pressed);
    bool RequestApproach(class AReaBullyWorldDirector* Director, class AReaBullyExplorer* Player);
    bool OriginalIdlePlaying() const;
    TArray<FTransform> ReadBones() const;
    bool SameBones(const TArray<FTransform>& Previous) const;
    void BeginGesture();
    void Fail(const FString& Reason);
    void Finish(const FString& Error = TEXT(""));
};
