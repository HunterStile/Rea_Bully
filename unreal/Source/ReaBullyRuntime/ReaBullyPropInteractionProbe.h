#pragma once
#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ReaBullyPropInteractionProbe.generated.h"

/** Command-line test of source-bound garages through actual controller F input. */
UCLASS(NotBlueprintable)
class AReaBullyPropInteractionProbe : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyPropInteractionProbe();
    FString ReportPath;
    FString PlanPath;
    virtual void Tick(float DeltaSeconds) override;
private:
    TArray<TSharedPtr<class FJsonValue>> Placements;
    TArray<TSharedPtr<class FJsonValue>> Records;
    TSharedPtr<class FJsonObject> Current;
    TWeakObjectPtr<class AReaBullyWorldProp> Target;
    TWeakObjectPtr<class AReaBullyExplorer> TestedPlayer;
    TMap<TWeakObjectPtr<class AReaBullyWorldProp>, int32> OtherCounts;
    TArray<FTransform> ClosedBones, OpenBones, FinalBones;
    FTransform SourceTransform;
    FVector ClosedCenter, ClosedExtent;
    FName Area, Zone;
    FString SourceId, SourceDigest;
    int32 Index = 0, Phase = 0, Approach = 0;
    float PhaseSeconds = 0;
    double Deadline = 0;
    uint64 KeyFrame = 0;
    bool bLoaded = false, bKeyHeld = false, bSawMoving = false;
    void Key(bool Pressed);
    bool RequestApproach(class AReaBullyWorldDirector* Director, class AReaBullyExplorer* Player);
    TArray<FTransform> ReadBones() const;
    bool SameBones(const TArray<FTransform>& Previous) const;
    bool AtClipEnd(class UAnimSequence* Clip) const;
    void Fail(const FString& Reason);
    void Finish(const FString& Error = TEXT(""));
};
