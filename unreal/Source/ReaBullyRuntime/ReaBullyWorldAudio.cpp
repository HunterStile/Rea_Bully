#include "ReaBullyWorldAudio.h"
#include "ReaBullyExploration.h"
#include "ReaBullyWorld.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Animation/BlendSpace.h"
#include "Components/AudioComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/SecureHash.h"
#include "Sound/SoundWave.h"

bool UReaBullyWorldAudioTools::VerifyAndConfigureWave(USoundWave* Wave, int32 SampleRate,
    int32 Channels, int32 SamplesPerChannel, const FString& PcmMD5, bool Loop)
{
#if WITH_EDITOR
    if (!Wave || SampleRate <= 0 || Channels != 1 || SamplesPerChannel <= 0 || PcmMD5.Len() != 32) return false;
    TArray<uint8> Pcm;
    uint32 ImportedRate = 0;
    uint16 ImportedChannels = 0;
    if (!Wave->GetImportedSoundWaveData(Pcm, ImportedRate, ImportedChannels) ||
        ImportedRate != static_cast<uint32>(SampleRate) || ImportedChannels != Channels ||
        Pcm.Num() != static_cast<int64>(SamplesPerChannel) * Channels * 2 ||
        !FMD5::HashBytes(Pcm.GetData(), Pcm.Num()).Equals(PcmMD5, ESearchCase::IgnoreCase)) return false;
    Wave->SetSoundAssetCompressionType(ESoundAssetCompressionType::PCM);
    Wave->SampleRateQuality = ESoundwaveSampleRateSettings::Max;
    Wave->OverrideLoadingBehavior(ESoundWaveLoadingBehavior::ForceInline);
    Wave->bLooping = Loop;
    Wave->MarkPackageDirty();
    return true;
#else
    return false;
#endif
}

bool UReaBullyWorldAudioTools::CrossedPhaseMarker(float Previous, float Current, float Marker)
{
    if (!FMath::IsFinite(Previous) || !FMath::IsFinite(Current) || !FMath::IsFinite(Marker) ||
        Previous < 0 || Previous >= 1 || Current < 0 || Current >= 1 || Marker < 0 || Marker >= 1 ||
        Previous == Current) return false;
    return Current > Previous ? Marker > Previous && Marker <= Current : Marker > Previous || Marker <= Current;
}

AReaBullyWorldAudio::AReaBullyWorldAudio()
{
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickGroup = TG_PostUpdateWork;
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("RecoveredAudioRoot"));
    StepA = CreateDefaultSubobject<UAudioComponent>(TEXT("OriginalStepEventA"));
    StepB = CreateDefaultSubobject<UAudioComponent>(TEXT("OriginalStepEventB"));
    Birds = CreateDefaultSubobject<UAudioComponent>(TEXT("OriginalBirdAmbience"));
    for (UAudioComponent* Component : {StepA.Get(), StepB.Get(), Birds.Get()})
    {
        Component->SetupAttachment(RootComponent);
        Component->bAutoActivate = false;
        Component->bAutoDestroy = false;
        Component->bIsUISound = false;
    }
    for (UAudioComponent* Component : {StepA.Get(), StepB.Get()})
    {
        Component->bAllowSpatialization = true;
        Component->bOverrideAttenuation = true;
        Component->AttenuationOverrides.bAttenuate = true;
        Component->AttenuationOverrides.bSpatialize = true;
        Component->AttenuationOverrides.AttenuationShapeExtents = FVector(500.0f, 0, 0);
        Component->AttenuationOverrides.FalloffDistance = 1200.0f;
    }
    Birds->bAllowSpatialization = false;
    Birds->bOverrideAttenuation = true;
    Birds->AttenuationOverrides.bAttenuate = false;
    Birds->AttenuationOverrides.bSpatialize = false;
}

void AReaBullyWorldAudio::BeginPlay()
{
    Super::BeginPlay();
    bValidData = Data && Data->Waves.Num() == 207 && Data->FootstepPhases.Num() == 3 &&
        Data->ExportReportSHA256.Len() == 64 && Data->ValidationReportSHA256.Len() == 64 &&
        Data->Waves.IsValidIndex(Data->BirdWaveIndex) &&
        !Data->ExteriorStepWaveIndices.IsEmpty() && !Data->InteriorStepWaveIndices.IsEmpty();
    if (bValidData)
    {
        for (int32 Index : Data->ExteriorStepWaveIndices)
            bValidData &= Data->Waves.IsValidIndex(Index) && Data->Waves[Index].Wave != nullptr;
        for (int32 Index : Data->InteriorStepWaveIndices)
            bValidData &= Data->Waves.IsValidIndex(Index) && Data->Waves[Index].Wave != nullptr;
        for (const FReaBullyFootstepPhase& Phase : Data->FootstepPhases)
            bValidData &= FMath::IsFinite(Phase.SourceDuration) && Phase.SourceDuration > 0 &&
                FMath::IsFinite(Phase.SourceSpeedCm) && Phase.SourceSpeedCm > 0 &&
                FMath::IsFinite(Phase.SourceMarkers.X) && FMath::IsFinite(Phase.SourceMarkers.Y) &&
                Phase.SourceMarkers.X >= 0 && Phase.SourceMarkers.Y >= 0 &&
                Phase.SourceMarkers.X < Phase.SourceDuration && Phase.SourceMarkers.Y < Phase.SourceDuration;
        bValidData &= Data->Waves[Data->BirdWaveIndex].Wave != nullptr;
    }
    if (!bValidData)
    {
        UE_LOG(LogTemp, Error, TEXT("REA_WORLD_AUDIO_INVALID missing verified waves or CAT phase data"));
        SetActorTickEnabled(false);
        return;
    }
    Birds->SetSound(Data->Waves[Data->BirdWaveIndex].Wave);
    UE_LOG(LogTemp, Display, TEXT("REA_WORLD_AUDIO_READY waves=207 policy=reconstructed_cue_selection_CAT_phase"));
}

bool AReaBullyWorldAudio::IsAmbiencePlaying() const { return Birds && Birds->IsPlaying(); }

void AReaBullyWorldAudio::PlayStep(int32 EventIndex, bool Exterior)
{
    const TArray<int32>& Choices = Exterior ? Data->ExteriorStepWaveIndices : Data->InteriorStepWaveIndices;
    const int32 Index = Choices[FootstepsPlayed % Choices.Num()];
    UAudioComponent* Component = EventIndex == 0 ? StepA.Get() : StepB.Get();
    FVector Position = Player->GetActorLocation() - FVector(0, 0, Player->GetCapsuleComponent()->GetScaledCapsuleHalfHeight());
    // Original 0x60e730 also queries both feet. Event A/B is not claimed as a left/right mapping.
    USkeletalMeshComponent* Mesh = Player->GetMesh();
    if (Mesh->GetBoneIndex(TEXT("LeftFoot")) >= 0 && Mesh->GetBoneIndex(TEXT("RightFoot")) >= 0)
    {
        const FVector Left = Mesh->GetBoneLocation(TEXT("LeftFoot"));
        const FVector Right = Mesh->GetBoneLocation(TEXT("RightFoot"));
        Position = Left.Z < Right.Z ? Left : Right;
    }
    Component->Stop();
    Component->SetWorldLocation(Position);
    Component->SetSound(Data->Waves[Index].Wave);
    Component->SetVolumeMultiplier(FMath::Clamp(FootstepVolume, 0.0f, 1.0f));
    Component->Play();
    ++FootstepsPlayed;
    if (FootstepsPlayed <= 4)
        UE_LOG(LogTemp, Display, TEXT("REA_WORLD_FOOTSTEP event=%d wave=%s phase=%f"),
            EventIndex, *Data->Waves[Index].OriginalName, PreviousPhase);
}

void AReaBullyWorldAudio::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bValidData) return;
    AReaBullyExplorer* CurrentPlayer = Cast<AReaBullyExplorer>(UGameplayStatics::GetPlayerPawn(this, 0));
    if (CurrentPlayer != Player.Get())
    {
        if (Player.IsValid())
        {
            RemoveTickPrerequisiteActor(Player.Get());
            RemoveTickPrerequisiteComponent(Player->GetMesh());
        }
        Player = CurrentPlayer;
        if (Player.IsValid())
        {
            AddTickPrerequisiteActor(Player.Get());
            AddTickPrerequisiteComponent(Player->GetMesh());
        }
        StepA->Stop(); StepB->Stop();
        PreviousPhase = -1; PreviousGait = -1;
    }
    if (!Director.IsValid())
        for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It) { Director = *It; break; }
    const bool Ready = Player.IsValid() && (!Director.IsValid() || Director->IsReady());
    const bool Exterior = !Director.IsValid() || Director->GetActiveArea() == TEXT("MainMap");
    const bool AudioAvailable = GEngine && GEngine->UseSound() && GetWorld()->bAllowAudioPlayback &&
        GetWorld()->GetAudioDeviceRaw() != nullptr;
    if (Ready && !AudioAvailable && !bAudioUnavailableReported)
    {
        bAudioUnavailableReported = true;
        UE_LOG(LogTemp, Display, TEXT("REA_WORLD_AUDIO_SUPPRESSED no active game audio device"));
    }
    if (Ready && AudioAvailable && Exterior && EnableAmbience)
    {
        // One request per area/readiness transition. IsPlaying may remain false
        // during async startup or a failed device; never retry once per frame.
        if (!bAmbienceRequested)
        {
            bAmbienceRequested = true;
            Birds->SetVolumeMultiplier(FMath::Clamp(AmbienceVolume, 0.0f, 1.0f));
            Birds->Play(); ++AmbienceStarts;
            UE_LOG(LogTemp, Display, TEXT("REA_WORLD_AMBIENCE_START reconstructed_MainMap_BirdLoop"));
        }
    }
    else if (bAmbienceRequested)
    {
        Birds->Stop();
        bAmbienceRequested = false;
    }
    if (!Ready || !AudioAvailable || Player->GetActionPhase() != EReaBullyActionPhase::Locomotion ||
        !Player->GetCharacterMovement()->IsMovingOnGround() || Player->GetVelocity().Size2D() < 40.0f)
    {
        PreviousPhase = -1; PreviousGait = -1;
        return;
    }
    UAnimSingleNodeInstance* Animation = Player->GetMesh()->GetSingleNodeInstance();
    if (!Animation || !Cast<UBlendSpace>(Animation->GetCurrentAsset())) { PreviousPhase = -1; return; }
    FVector Position, Filtered;
    Animation->GetBlendSpaceState(Position, Filtered);
    if (Filtered.ContainsNaN() || !FMath::IsFinite(Animation->GetCurrentTime()))
    {
        PreviousPhase = -1; PreviousGait = -1;
        return;
    }
    // Nearest source gait is an operational approximation to the original blend policy.
    int32 Gait = 0;
    for (int32 Index = 1; Index < Data->FootstepPhases.Num(); ++Index)
        if (FMath::Abs(Filtered.X - Data->FootstepPhases[Index].SourceSpeedCm) <
            FMath::Abs(Filtered.X - Data->FootstepPhases[Gait].SourceSpeedCm)) Gait = Index;
    // UE's SingleNode BlendSpace time and length are normalized to [0,1].
    const float Current = FMath::Fmod(Animation->GetCurrentTime(), 1.0f);
    const FReaBullyFootstepPhase& Source = Data->FootstepPhases[Gait];
    if (PreviousPhase >= 0 && PreviousGait == Gait && DeltaSeconds < .3f)
    {
        if (UReaBullyWorldAudioTools::CrossedPhaseMarker(PreviousPhase, Current, Source.SourceMarkers.X / Source.SourceDuration)) PlayStep(0, Exterior);
        if (UReaBullyWorldAudioTools::CrossedPhaseMarker(PreviousPhase, Current, Source.SourceMarkers.Y / Source.SourceDuration)) PlayStep(1, Exterior);
    }
    PreviousPhase = Current;
    PreviousGait = Gait;
}

void AReaBullyWorldAudio::EndPlay(const EEndPlayReason::Type Reason)
{
    SetActorTickEnabled(false);
    bValidData = false;
    bAmbienceRequested = false;
    if (Player.IsValid())
    {
        RemoveTickPrerequisiteActor(Player.Get());
        RemoveTickPrerequisiteComponent(Player->GetMesh());
    }
    StepA->Stop(); StepB->Stop(); Birds->Stop();
    Super::EndPlay(Reason);
}
