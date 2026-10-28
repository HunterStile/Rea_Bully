#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "GameFramework/Actor.h"
#include "ReaBullyWorldDoor.generated.h"

class USkeletalMesh;
class USkeletalMeshComponent;
class UAnimSequence;
class UBoxComponent;
class APlayerController;

/** Source model and its own AGR clips. Interaction/closed-box collision are reconstruction. */
UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyWorldDoorData : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) FString ModelKey;
    UPROPERTY(EditAnywhere) FString SourceSHA256;
    UPROPERTY(EditAnywhere) TObjectPtr<USkeletalMesh> Mesh;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> OpenClip;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> CloseClip;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> ClosedClip;
    UPROPERTY(EditAnywhere) FString OpenOriginal;
    UPROPERTY(EditAnywhere) FString CloseOriginal;
    UPROPERTY(EditAnywhere) FString ClosedOriginal;
    /** Source GLB skin evaluated at original IDLE(0), in imported UE local cm. */
    UPROPERTY(EditAnywhere) FVector ClosedPoseCenterCm = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) FVector ClosedPoseExtentCm = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) FString ClosedPoseSourceSHA256;
    UPROPERTY(EditAnywhere) FString ClosedPoseOriginalClip;
};

/** DAT-created nonmission door. No inferred ACT scheduling, saved locks or paired leaves. */
UCLASS()
class REABULLYRUNTIME_API AReaBullyWorldDoor : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyWorldDoor();
    UPROPERTY(VisibleAnywhere) TObjectPtr<USkeletalMeshComponent> MeshComponent;
    UPROPERTY(VisibleAnywhere) TObjectPtr<UBoxComponent> ClosedBlocker;
    UPROPERTY(EditAnywhere) TObjectPtr<UReaBullyWorldDoorData> Data;
    UPROPERTY(EditAnywhere) FString SourceReference;
    UPROPERTY(EditAnywhere) FString SourceName;
    UPROPERTY(EditAnywhere) int32 SourceAreaCode = -1;
    UPROPERTY(EditAnywhere) bool SourceLockedRaw = false;
    UPROPERTY(EditAnywhere) bool bExplorationUnlocked = true;
    UPROPERTY(EditAnywhere) float InteractionDistanceCm = 220.0f;
    UPROPERTY(EditAnywhere) bool bReconstructedClosedCollision = true;
    UPROPERTY(EditAnywhere) bool bHasTravelTarget = false;
    UPROPERTY(EditAnywhere) FVector TravelDestinationCm = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) FName TravelArea;
    UPROPERTY(EditAnywhere) float TravelYawDegrees = 0.0f;
    UFUNCTION(BlueprintCallable, CallInEditor, Category="Recovered world")
    bool ConfigureRecoveredDoor(UReaBullyWorldDoorData* Source);
    UFUNCTION(BlueprintCallable, Category="Recovered world") bool RequestToggle();
    UFUNCTION(BlueprintPure, Category="Recovered world") bool IsOpen() const { return bOpen; }
    UFUNCTION(BlueprintPure, Category="Recovered world") bool IsAnimating() const { return bAnimating; }
    UFUNCTION(BlueprintPure, Category="Recovered world") bool IsMoving() const { return bAnimating; }
    /** Accepted OPEN/CLOSE motions since this source was configured. */
    UFUNCTION(BlueprintPure, Category="Recovered world") int32 GetToggleCount() const { return ToggleCount; }
    /** Travel requests accepted by the director; arrival readiness is audited separately. */
    UFUNCTION(BlueprintPure, Category="Recovered world") int32 GetSuccessfulTravels() const { return SuccessfulTravels; }
    /** Read-only snapshot for the integration probe; no injected input or provenance bypass. */
    UFUNCTION(BlueprintPure, Category="Recovered world") FString GetInteractionDiagnostics(APlayerController* Controller) const;
    virtual void OnConstruction(const FTransform& Transform) override;
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;

private:
    UPROPERTY(Transient) TObjectPtr<UAnimSequence> ActiveClip;
    bool bConfigured = false;
    bool bOpen = false;
    bool bAnimating = false;
    bool bTargetOpen = false;
    float ClipSeconds = 0.0f;
    int32 ToggleCount = 0;
    int32 SuccessfulTravels = 0;
    void SetPose(UAnimSequence* Clip, float Seconds);
    void UpdateClosedCollision();
    bool DoorwayOccupied() const;
    bool IsNearestDoor(APlayerController* Controller) const;
    bool TryTravel();
    bool IsWorldReadyForInteraction() const;
};
