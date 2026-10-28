#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "GameFramework/Actor.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "ReaBullyWorldAudio.generated.h"

class USoundWave;
class UAudioComponent;
class AReaBullyExplorer;
class AReaBullyWorldDirector;

USTRUCT(BlueprintType)
struct FReaBullyOriginalWave
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere) FString Bank;
    UPROPERTY(EditAnywhere) int32 OriginalIndex = -1;
    UPROPERTY(EditAnywhere) FString OriginalName;
    UPROPERTY(EditAnywhere) TObjectPtr<USoundWave> Wave;
    UPROPERTY(EditAnywhere) FString WavSHA256;
    UPROPERTY(EditAnywhere) FString OriginalMetadataJson;
};

USTRUCT(BlueprintType)
struct FReaBullyFootstepPhase
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere) FString OriginalClip;
    UPROPERTY(EditAnywhere) FString OriginalCue;
    UPROPERTY(EditAnywhere) float SourceDuration = 0.0f;
    UPROPERTY(EditAnywhere) float SourceSpeedCm = 0.0f;
    UPROPERTY(EditAnywhere) FVector2D SourceMarkers = FVector2D::ZeroVector;
    UPROPERTY(EditAnywhere) FString SourceEvidenceJson;
};

/** Original waves; reconstructed cue selection is kept separate from provenance. */
UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyWorldAudioData : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) FString ExportReportSHA256;
    UPROPERTY(EditAnywhere) FString ValidationReportSHA256;
    UPROPERTY(EditAnywhere) FString PlaybackPolicyJson;
    UPROPERTY(EditAnywhere) TArray<FReaBullyOriginalWave> Waves;
    UPROPERTY(EditAnywhere) TArray<FReaBullyFootstepPhase> FootstepPhases;
    UPROPERTY(EditAnywhere) TArray<int32> ExteriorStepWaveIndices;
    UPROPERTY(EditAnywhere) TArray<int32> InteriorStepWaveIndices;
    UPROPERTY(EditAnywhere) int32 BirdWaveIndex = -1;
};

/** Follows actual player animation phase, with explicit prototype audio policy. */
UCLASS()
class REABULLYRUNTIME_API AReaBullyWorldAudio : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyWorldAudio();
    UPROPERTY(EditAnywhere) TObjectPtr<UReaBullyWorldAudioData> Data;
    UPROPERTY(EditAnywhere) float FootstepVolume = 0.65f;
    UPROPERTY(EditAnywhere) float AmbienceVolume = 0.18f;
    UPROPERTY(EditAnywhere) bool EnableAmbience = true;
    virtual void BeginPlay() override;
    virtual void Tick(float DeltaSeconds) override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
    UFUNCTION(BlueprintPure, Category="Recovered audio") int32 GetFootstepsPlayed() const { return FootstepsPlayed; }
    UFUNCTION(BlueprintPure, Category="Recovered audio") int32 GetAmbienceStarts() const { return AmbienceStarts; }
    UFUNCTION(BlueprintPure, Category="Recovered audio") bool IsAmbiencePlaying() const;
private:
    UPROPERTY(VisibleAnywhere) TObjectPtr<UAudioComponent> StepA;
    UPROPERTY(VisibleAnywhere) TObjectPtr<UAudioComponent> StepB;
    UPROPERTY(VisibleAnywhere) TObjectPtr<UAudioComponent> Birds;
    TWeakObjectPtr<AReaBullyExplorer> Player;
    TWeakObjectPtr<AReaBullyWorldDirector> Director;
    float PreviousPhase = -1.0f;
    int32 PreviousGait = -1;
    int32 FootstepsPlayed = 0;
    int32 AmbienceStarts = 0;
    bool bValidData = false;
    bool bAmbienceRequested = false;
    bool bAudioUnavailableReported = false;
    void PlayStep(int32 EventIndex, bool Exterior);
};

UCLASS()
class REABULLYRUNTIME_API UReaBullyWorldAudioTools : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    /** Checks saved imported PCM, not just today's source file; never plays audio. */
    UFUNCTION(BlueprintCallable, Category="Recovered audio")
    static bool VerifyAndConfigureWave(USoundWave* Wave, int32 SampleRate, int32 Channels,
        int32 SamplesPerChannel, const FString& PcmMD5, bool Loop);
    UFUNCTION(BlueprintPure, Category="Recovered audio")
    static bool CrossedPhaseMarker(float Previous, float Current, float Marker);
};
