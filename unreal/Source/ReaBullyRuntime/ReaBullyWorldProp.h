#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "GameFramework/Actor.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "ReaBullyWorldProp.generated.h"

class USkeletalMesh;
class UStaticMesh;
class USkeletalMeshComponent;
class UAnimSequence;
class APlayerController;

/** One original object skin, with at most one explicitly selected ambient clip. */
UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyWorldPropData : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) FString ModelKey;
    UPROPERTY(EditAnywhere) FString SourceSHA256;
    UPROPERTY(EditAnywhere) TObjectPtr<USkeletalMesh> Mesh;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> AmbientLoop;
    UPROPERTY(EditAnywhere) FString AmbientLoopOriginal;
};

/** Explicit exploration interaction; source clips do not authorize autoplay. */
UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyPropInteractionData : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) FString ModelKey;
    UPROPERTY(EditAnywhere) FString SourceSHA256;
    UPROPERTY(EditAnywhere) FString EvidenceSHA256;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> ClosedClip;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> OpenClip;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> CloseClip;
    UPROPERTY(EditAnywhere) FString ClosedOriginal;
    UPROPERTY(EditAnywhere) FString OpenOriginal;
    UPROPERTY(EditAnywhere) FString CloseOriginal;
    UPROPERTY(EditAnywhere) FVector ClosedPoseCenterCm = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) FVector ClosedPoseExtentCm = FVector::ZeroVector;
};

/** One manual carnival invitation, then the existing original idle loop. */
UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyPropGestureData : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) FString ModelKey;
    UPROPERTY(EditAnywhere) FString SourceSHA256;
    UPROPERTY(EditAnywhere) FString EvidenceSHA256;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> IdleClip;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> GestureClip;
    UPROPERTY(EditAnywhere) FString IdleOriginal;
    UPROPERTY(EditAnywhere) FString GestureOriginal;
    UPROPERTY(EditAnywhere) FVector IdlePoseCenterCm = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) FVector IdlePoseExtentCm = FVector::ZeroVector;
};

/** Source bind pose/selected loops, or an explicitly configured player action. */
UCLASS()
class REABULLYRUNTIME_API AReaBullyWorldProp : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyWorldProp();
    UPROPERTY(VisibleAnywhere) TObjectPtr<USkeletalMeshComponent> MeshComponent;
    UPROPERTY(EditAnywhere) TObjectPtr<UReaBullyWorldPropData> Data;
    UPROPERTY(EditAnywhere) FString SourceReference;
    UPROPERTY(EditAnywhere) TObjectPtr<UReaBullyPropInteractionData> InteractionData;
    UPROPERTY(EditAnywhere) TObjectPtr<UReaBullyPropGestureData> GestureData;
    UPROPERTY(EditAnywhere) FString InteractionSourceId;
    UPROPERTY(EditAnywhere) bool bExplorationInteractionEnabled = false;
    UPROPERTY(EditAnywhere) float InteractionDistanceCm = 220.0f;
    UFUNCTION(BlueprintCallable, CallInEditor, Category="Recovered world")
    bool ConfigureRecoveredProp(UReaBullyWorldPropData* Source);
    UFUNCTION(BlueprintCallable, Category="Recovered world")
    bool RequestInteractionToggle();
    UFUNCTION(BlueprintCallable, Category="Recovered world")
    FVector GetInteractionWorldLocation() const;
    bool IsInteractionOpen() const { return bInteractionOpen; }
    bool IsInteractionMoving() const { return bInteractionMoving; }
    int32 GetInteractionCount() const { return InteractionCount; }
    int32 GetCompletedGestureCount() const { return CompletedGestureCount; }
    float GetLastGestureEndpointSeconds() const { return LastGestureEndpointSeconds; }
    virtual void OnConstruction(const FTransform& Transform) override;
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
private:
    bool bInteractionConfigured = false;
    bool bInteractionOpen = false;
    bool bInteractionMoving = false;
    bool bInteractionTargetOpen = false;
    int32 InteractionCount = 0;
    int32 CompletedGestureCount = 0;
    float LastGestureEndpointSeconds = 0.0f;
    float InteractionSeconds = 0.0f;
    UPROPERTY(Transient) TObjectPtr<UAnimSequence> InteractionClip;
    void SetInteractionPose(UAnimSequence* Clip, float Seconds);
    bool IsWorldReadyForInteraction() const;
    bool IsNearestInteraction(APlayerController* Controller) const;
    float InteractionDistanceSquared(FVector WorldPosition) const;
};

/** Reads the imported mesh's own reference skeleton for the importer audit. */
UCLASS()
class REABULLYRUNTIME_API UReaBullyWorldPropTools : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable, Category="Recovered assets")
    static TArray<FString> GetMeshBoneNames(USkeletalMesh* Mesh);
    UFUNCTION(BlueprintCallable, Category="Recovered assets")
    static TArray<int32> GetMeshBoneParents(USkeletalMesh* Mesh);
    /** Read-only asset diagnostics; does not initialize a world or cook physics. */
    UFUNCTION(BlueprintCallable, Category="Recovered assets")
    static FString InspectCollisionMesh(UStaticMesh* Mesh);
    /** Read every imported collision face before Chaos cooking, for source comparison. */
    UFUNCTION(BlueprintCallable, Category="Recovered assets")
    static FString ReadCollisionTriangleGeometry(UStaticMesh* Mesh);
};
