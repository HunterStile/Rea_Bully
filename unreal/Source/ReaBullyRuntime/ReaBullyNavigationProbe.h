#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ReaBullyNavigationProbe.generated.h"

struct FKey;

/** Real controller-input check of focus, cancellation and two verified area entries. */
UCLASS(NotBlueprintable)
class AReaBullyNavigationProbe : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyNavigationProbe();
    FString ReportPath;
    virtual void Tick(float DeltaSeconds) override;
private:
    TSharedPtr<class FJsonObject> Report;
    TArray<TSharedPtr<class FJsonValue>> TravelRecords;
    int32 Phase = 0;
    float PhaseSeconds = 0;
    double Deadline = 0;
    FVector StartPosition = FVector::ZeroVector;
    FRotator StartRotation = FRotator::ZeroRotator;
    FString SelectedId;
    FName SelectedArea;
    FVector SelectedFeet;
    TWeakObjectPtr<class AReaBullyExplorer> TestedPlayer;
    void Key(const FKey& Key, bool Pressed);
    void Tap(const FKey& Key);
    void Finish(const FString& Error = TEXT(""));
};
