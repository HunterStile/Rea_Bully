#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "GameFramework/Actor.h"
#include "ReaBullyWorld.generated.h"

class UStaticMesh;
class ULevelStreamingDynamic;

/** One original placement zone, saved as a streamable Unreal level. */
USTRUCT(BlueprintType)
struct FReaBullyWorldZone
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere) FName Name;
    UPROPERTY(EditAnywhere) FString DisplayName;
    UPROPERTY(EditAnywhere) TSoftObjectPtr<UWorld> Level;
    UPROPERTY(EditAnywhere) FVector Minimum = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) FVector Maximum = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) bool Exterior = true;
    UPROPERTY(EditAnywhere) bool AlwaysLoaded = false;
    UPROPERTY(EditAnywhere) FName Area;
};

UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyWorldData : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) TArray<FReaBullyWorldZone> Zones;
    UPROPERTY(EditAnywhere) FVector Start = FVector(25000.0, 7300.0, 705.0);
    UPROPERTY(EditAnywhere) float LoadDistance = 13000.0f;
    UPROPERTY(EditAnywhere) float UnloadDistance = 19000.0f;
};

/** Reuses each original shape across its placements, preserving the full TRS. */
UCLASS()
class REABULLYRUNTIME_API AReaBullyZoneGeometry : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyZoneGeometry();
    UFUNCTION(BlueprintCallable, CallInEditor, Category="Recovered world")
    int32 AddOriginalInstances(UStaticMesh* Mesh, const TArray<FTransform>& Transforms,
        bool VisualOnly, bool ReverseCulling);
    UFUNCTION(BlueprintCallable, CallInEditor, Category="Recovered world")
    int32 AddOriginalCollisionInstances(UStaticMesh* Mesh, const TArray<FTransform>& Transforms);
};

/** Distance-based reconstruction of outdoor streaming; original schedule is not claimed. */
UCLASS()
class REABULLYRUNTIME_API AReaBullyWorldDirector : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyWorldDirector();
    UPROPERTY(EditAnywhere) TObjectPtr<UReaBullyWorldData> Data;
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    bool IsReady() const { return bReady; }
    int32 VisibleZones() const;
    TArray<FName> VisibleZoneNames() const;
    FName GetActiveArea() const { return ActiveArea; }
    UFUNCTION(BlueprintCallable, Category="Recovered world")
    bool RequestTravel(FVector Destination, FName Area);
private:
    UPROPERTY(Transient) TArray<TObjectPtr<ULevelStreamingDynamic>> Streams;
    bool bReady = false;
    bool bInputLocked = false;
    float PollTime = 0.0f;
    FVector PendingDestination = FVector::ZeroVector;
    FName ActiveArea = TEXT("MainMap");
    void UpdateStreaming();
    void SetPlayerLocked(bool Locked);
};
