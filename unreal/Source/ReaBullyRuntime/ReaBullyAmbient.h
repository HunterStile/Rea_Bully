#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "GameFramework/Character.h"
#include "ReaBullyAmbient.generated.h"

class USkeletalMesh;
class UBlendSpace1D;
class UAnimSequence;

/** Each original ped keeps its own recovered skin and bind pose. */
UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyAmbientPedData : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) int32 PedId = 0;
    UPROPERTY(EditAnywhere) TObjectPtr<USkeletalMesh> Mesh;
    UPROPERTY(EditAnywhere) TObjectPtr<UBlendSpace1D> Locomotion;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> AmbientIdle;
    UPROPERTY(EditAnywhere) float HalfHeight = 96.0f;
    UPROPERTY(EditAnywhere) float WalkSpeed = 160.0f;
};

USTRUCT(BlueprintType)
struct FReaBullyAmbientPoint
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere) FVector Position = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) float WaitSeconds = 0.0f;
};

/** Reconstructed traversal of original patrol points, with real floor/collision. */
UCLASS()
class REABULLYRUNTIME_API AReaBullyAmbientPed : public ACharacter
{
    GENERATED_BODY()
public:
    AReaBullyAmbientPed();
    UPROPERTY(EditAnywhere) TObjectPtr<UReaBullyAmbientPedData> Data;
    UPROPERTY(EditAnywhere) TArray<FReaBullyAmbientPoint> Route;
    UPROPERTY(EditAnywhere) FString SourceReference;
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    float DistanceWalked() const { return TotalDistance; }
private:
    int32 Target = 0;
    int32 Direction = 1;
    float WaitRemaining = 0.0f;
    float AmbientRemaining = 0.0f;
    float IdleSeconds = 0.0f;
    float TotalDistance = 0.0f;
    FVector PreviousPosition = FVector::ZeroVector;
    bool bPlacedOnOriginalFloor = false;
    void SetLocomotion();
};
