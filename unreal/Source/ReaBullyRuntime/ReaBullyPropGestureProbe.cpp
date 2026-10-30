#include "ReaBullyPropGestureProbe.h"
#include "ReaBullyWorld.h"
#include "ReaBullyWorldProp.h"
#include "ReaBullyExploration.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/PlayerController.h"
#include "Engine/Level.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "InputKeyEventArgs.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UnrealClient.h"

namespace
{
bool GestureVector(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, FVector& Value)
{
    const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
    return Object.IsValid() && Object->TryGetArrayField(Field, Items) && Items->Num() == 3 &&
        (*Items)[0]->TryGetNumber(Value.X) && (*Items)[1]->TryGetNumber(Value.Y) &&
        (*Items)[2]->TryGetNumber(Value.Z) && !Value.ContainsNaN();
}
}

AReaBullyPropGestureProbe::AReaBullyPropGestureProbe()
{
    PrimaryActorTick.bCanEverTick = true;
}

void AReaBullyPropGestureProbe::Key(bool Pressed)
{
    bKeyHeld = Pressed;
    KeySeconds = 0;
    if (auto* Controller = GetWorld()->GetFirstPlayerController())
        Controller->InputKey(FInputKeyEventArgs(nullptr, FInputDeviceId::CreateFromInternalId(0), EKeys::F,
            Pressed ? IE_Pressed : IE_Released, Pressed ? 1.0f : 0.0f, false, FPlatformTime::Cycles64()));
}

bool AReaBullyPropGestureProbe::RequestApproach(AReaBullyWorldDirector* Director, AReaBullyExplorer* Player)
{
    FVector Local = IdleCenter;
    Local.Z -= IdleExtent.Z;
    // Diagnostic viewing/proximity positions only, not recovered gameplay spawns.
    if (Approach < 2) Local.Y += Approach == 0 ? -180 : 180;
    else Local.X += Approach == 2 ? -180 : 180;
    const FVector Feet = SourceTransform.TransformPosition(Local);
    Current->SetNumberField(TEXT("diagnostic_approach"), Approach);
    Current->SetStringField(TEXT("diagnostic_approach_feet_cm"), Feet.ToString());
    Deadline = FPlatformTime::Seconds() + 90;
    Player->GetCharacterMovement()->StopMovementImmediately();
    return Director->RequestTravel(Feet + FVector(0, 0,
        Player->GetCapsuleComponent()->GetScaledCapsuleHalfHeight() + 2), TEXT("MainMap"));
}

bool AReaBullyPropGestureProbe::OriginalIdlePlaying() const
{
    auto* Instance = Target.IsValid() ? Target->MeshComponent->GetSingleNodeInstance() : nullptr;
    return Instance && Target->GestureData && Instance->GetCurrentAsset() == Target->GestureData->IdleClip &&
        Instance->IsLooping() && Instance->IsPlaying() && !Target->IsInteractionMoving();
}

TArray<FTransform> AReaBullyPropGestureProbe::ReadBones() const
{
    TArray<FTransform> Bones;
    if (Target.IsValid())
        for (int32 Bone = 0; Bone < Target->MeshComponent->GetNumBones(); ++Bone)
            Bones.Add(Target->MeshComponent->GetBoneTransform(Bone));
    return Bones;
}

bool AReaBullyPropGestureProbe::SameBones(const TArray<FTransform>& Previous) const
{
    const auto Now = ReadBones();
    if (Now.IsEmpty() || Now.Num() != Previous.Num()) return false;
    for (int32 Bone = 0; Bone < Now.Num(); ++Bone)
        if (!Now[Bone].Equals(Previous[Bone], .01f)) return false;
    return true;
}

void AReaBullyPropGestureProbe::BeginGesture()
{
    bSawGesture = bBonesChanged = false;
    MovingBones.Reset();
    Key(true);
    Phase = 3; PhaseSeconds = 0; Deadline = FPlatformTime::Seconds() + 30;
}

void AReaBullyPropGestureProbe::Fail(const FString& Reason)
{
    Key(false);
    Current->SetBoolField(TEXT("passed"), false);
    Current->SetStringField(TEXT("error"), Reason);
    Current->SetNumberField(TEXT("phase_at_failure"), Phase);
    if (Target.IsValid())
    {
        Current->SetNumberField(TEXT("actions_at_failure"), Target->GetInteractionCount());
        Current->SetNumberField(TEXT("completed_at_failure"), Target->GetCompletedGestureCount());
        Current->SetBoolField(TEXT("moving_at_failure"), Target->IsInteractionMoving());
    }
    Records.Add(MakeShared<FJsonValueObject>(Current));
    UE_LOG(LogTemp, Warning, TEXT("REA_GESTURE_PROBE %s FAIL %s"), *SourceId, *Reason);
    ++Index; Phase = 0;
}

void AReaBullyPropGestureProbe::Finish(const FString& Error)
{
    Key(false);
    SetActorTickEnabled(false);
    bool Passed = Error.IsEmpty() && Placements.Num() == 6 && Records.Num() == 6;
    for (const auto& Record : Records) Passed &= Record->AsObject()->GetBoolField(TEXT("passed"));
    auto Report = MakeShared<FJsonObject>();
    Report->SetBoolField(TEXT("passed"), Passed);
    Report->SetStringField(TEXT("error"), Error);
    Report->SetStringField(TEXT("plan"), PlanPath);
    Report->SetStringField(TEXT("scope"), TEXT("Six original carnival props: two actual held-F presses each, own one-shot CMERE bone motion and return to existing idle; no original autonomous scheduler or speech claim."));
    Report->SetArrayField(TEXT("placements"), Records);
    FString Json;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Json));
    if (!FFileHelper::SaveStringToFile(Json, *ReportPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) Passed = false;
    UE_LOG(LogTemp, Display, TEXT("REA_GESTURE_PROBE %s"), Passed ? TEXT("PASS") : TEXT("FAIL"));
    FPlatformMisc::RequestExitWithStatus(false, Passed ? 0 : 1);
}

void AReaBullyPropGestureProbe::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (bKeyHeld)
    {
        KeySeconds += DeltaSeconds;
        if (KeySeconds >= 1.6f) Key(false); // Longer than CMERE: holding F must not retrigger.
    }
    if (!bLoaded)
    {
        FString Json;
        TSharedPtr<FJsonObject> Plan;
        const TArray<TSharedPtr<FJsonValue>>* Rows = nullptr;
        const TArray<TSharedPtr<FJsonValue>>* ModelRows = nullptr;
        if (!FFileHelper::LoadFileToString(Json, *PlanPath) ||
            !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Plan) || !Plan.IsValid() ||
            !Plan->TryGetArrayField(TEXT("placements"), Rows) || Rows->Num() != 6 ||
            !Plan->TryGetArrayField(TEXT("models"), ModelRows) || ModelRows->Num() != 3)
        { Finish(TEXT("Invalid verified gesture plan")); return; }
        Placements = *Rows;
        for (const auto& Value : *ModelRows)
        {
            FString KeyName;
            const auto Model = Value->AsObject();
            if (!Model.IsValid() || !Model->TryGetStringField(TEXT("model_key"), KeyName) || Models.Contains(KeyName))
            { Finish(TEXT("Duplicate or invalid source gesture model")); return; }
            Models.Add(KeyName, Model);
        }
        bLoaded = true; Deadline = FPlatformTime::Seconds() + 120;
    }
    if (Index >= Placements.Num()) { Finish(); return; }
    auto* Controller = GetWorld()->GetFirstPlayerController();
    auto* Player = Controller ? Cast<AReaBullyExplorer>(Controller->GetPawn()) : nullptr;
    AReaBullyWorldDirector* Director = nullptr;
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It) { Director = *It; break; }
    if (!Player || !Director)
    {
        if (FPlatformTime::Seconds() > Deadline) Finish(TEXT("Player or world director missing"));
        return;
    }
    if (Phase >= 2 && Player != TestedPlayer.Get()) { Fail(TEXT("Player respawned during gesture verification")); return; }
    if (Phase == 0)
    {
        const auto Row = Placements[Index]->AsObject();
        const TSharedPtr<FJsonObject>* Transform = nullptr;
        const TArray<TSharedPtr<FJsonValue>>* Quaternion = nullptr;
        FVector Translation, Scale;
        FQuat Rotation;
        FString Area, Zone;
        if (!Row.IsValid() || !Row->TryGetStringField(TEXT("source_id"), SourceId) ||
            !Row->TryGetStringField(TEXT("model_key"), ModelKey) || !Models.Contains(ModelKey) ||
            !Row->TryGetStringField(TEXT("area"), Area) || Area != TEXT("MainMap") ||
            !Row->TryGetStringField(TEXT("zone"), Zone) || Zone != TEXT("zone_carn1") ||
            !Row->TryGetObjectField(TEXT("transform"), Transform) ||
            !GestureVector(*Transform, TEXT("translation_cm"), Translation) || !GestureVector(*Transform, TEXT("scale_xyz"), Scale) ||
            !(*Transform)->TryGetArrayField(TEXT("rotation_quaternion_xyzw"), Quaternion) || Quaternion->Num() != 4 ||
            !(*Quaternion)[0]->TryGetNumber(Rotation.X) || !(*Quaternion)[1]->TryGetNumber(Rotation.Y) ||
            !(*Quaternion)[2]->TryGetNumber(Rotation.Z) || !(*Quaternion)[3]->TryGetNumber(Rotation.W) ||
            Rotation.ContainsNaN() || !Rotation.IsNormalized())
        { Finish(TEXT("Invalid original carnival placement")); return; }
        const TSharedPtr<FJsonObject>* Bounds = nullptr;
        if (!Models[ModelKey]->TryGetStringField(TEXT("source_sha256"), SourceDigest) ||
            !Models[ModelKey]->TryGetObjectField(TEXT("idle_bounds_cm"), Bounds) ||
            !GestureVector(*Bounds, TEXT("center"), IdleCenter) || !GestureVector(*Bounds, TEXT("extent"), IdleExtent))
        { Finish(TEXT("Invalid original idle skin bounds")); return; }
        Current = MakeShared<FJsonObject>();
        Current->SetObjectField(TEXT("source_placement"), Row);
        Current->SetStringField(TEXT("approach_policy"), TEXT("Diagnostic +/-180cm local horizontal offsets from original idle skin box; not original spawn positions."));
        SourceTransform = FTransform(Rotation, Translation, Scale);
        Approach = Attempt = 0; Target.Reset(); OtherCounts.Reset();
        if (!RequestApproach(Director, Player)) { Fail(TEXT("MainMap unavailable")); return; }
        Phase = 1; PhaseSeconds = 0;
        return;
    }
    if (FPlatformTime::Seconds() > Deadline)
    {
        if (Phase == 1 && Approach < 3)
        {
            ++Approach;
            if (RequestApproach(Director, Player)) return;
        }
        Fail(TEXT("Carnival gesture phase timed out")); return;
    }
    if (!Director->IsReady()) return;
    if (Director->GetActiveArea() != TEXT("MainMap")) { Fail(TEXT("Unexpected area travel")); return; }
    PhaseSeconds += DeltaSeconds;
    if (Phase == 1)
    {
        if (!Player->GetCharacterMovement()->IsMovingOnGround()) return;
        if (!Director->VisibleZoneNames().Contains(FName(TEXT("zone_carn1")))) { Fail(TEXT("Source carnival zone not visible")); return; }
        int32 Matches = 0;
        for (TActorIterator<AReaBullyWorldProp> It(GetWorld()); It; ++It)
            if (It->GetLevel() && It->GetLevel()->bIsVisible)
            {
                if (It->InteractionSourceId == SourceId) { Target = *It; ++Matches; }
                else if (It->bExplorationInteractionEnabled) OtherCounts.Add(*It, It->GetInteractionCount());
            }
        if (Matches != 1 || !Target.IsValid() || Target->IsHidden() || !Target->Data || !Target->GestureData ||
            Target->InteractionData || Target->Data->ModelKey != ModelKey || Target->Data->SourceSHA256 != SourceDigest ||
            Target->GestureData->SourceSHA256 != SourceDigest || !Target->bExplorationInteractionEnabled ||
            !Target->GetActorTransform().Equals(SourceTransform, .01f))
        { Fail(TEXT("Original attendant and gesture binding differ from source plan")); return; }
        if (!Cast<AReaBullyZoneGeometry>(Player->GetCharacterMovement()->CurrentFloor.HitResult.GetActor()))
        { Fail(TEXT("Player not grounded on recovered geometry")); return; }
        TestedPlayer = Player;
        Controller->SetControlRotation((Target->GetInteractionWorldLocation() - Player->GetActorLocation()).Rotation());
        Current->SetStringField(TEXT("player_position_cm"), Player->GetActorLocation().ToString());
        Current->SetStringField(TEXT("floor_component"), GetPathNameSafe(Player->GetCharacterMovement()->CurrentFloor.HitResult.GetComponent()));
        Phase = 2; PhaseSeconds = 0; Deadline = FPlatformTime::Seconds() + 30;
        return;
    }
    if (!Target.IsValid()) { Fail(TEXT("Attendant unloaded during input verification")); return; }
    if (Phase == 2 && PhaseSeconds >= .8f)
    {
        if (!OriginalIdlePlaying() || Target->GetInteractionCount() || Target->GetCompletedGestureCount())
        { Fail(TEXT("Gesture fired before manual input or original idle absent")); return; }
        Current->SetBoolField(TEXT("no_gesture_autoplay"), true);
        BeginGesture();
        return;
    }
    if (Phase == 3)
    {
        const int32 Expected = Attempt + 1;
        if (Target->GetInteractionCount() > Expected || Target->GetCompletedGestureCount() > Expected)
        { Fail(TEXT("Holding F retriggered the one-shot gesture")); return; }
        if (Target->IsInteractionMoving())
        {
            auto* Instance = Target->MeshComponent->GetSingleNodeInstance();
            if (!Instance || Instance->GetCurrentAsset() != Target->GestureData->GestureClip || Instance->IsLooping())
            { Fail(TEXT("Wrong or looping gesture animation")); return; }
            bSawGesture = true;
            if (MovingBones.IsEmpty()) MovingBones = ReadBones();
            else bBonesChanged |= !SameBones(MovingBones);
            FString Screenshot;
            if (Index == 0 && Attempt == 0 && PhaseSeconds > .3f && PhaseSeconds < .4f &&
                FParse::Value(FCommandLine::Get(), TEXT("ReaBullyScreenshot="), Screenshot))
                FScreenshotRequest::RequestScreenshot(Screenshot, true, false);
        }
        if (PhaseSeconds < 1.7f || bKeyHeld || Target->GetCompletedGestureCount() != Expected) return;
        if (!bSawGesture || !bBonesChanged || !OriginalIdlePlaying() || Target->GetInteractionCount() != Expected ||
            Target->IsInteractionOpen() || !FMath::IsNearlyEqual(Target->GetLastGestureEndpointSeconds(),
                Target->GestureData->GestureClip->GetPlayLength(), .001f))
        { Fail(TEXT("F gesture did not reach its source endpoint and return to original idle")); return; }
        Current->SetBoolField(Attempt == 0 ? TEXT("first_f_gesture_completed") : TEXT("second_f_gesture_completed"), true);
        Current->SetNumberField(TEXT("original_gesture_endpoint_seconds"), Target->GetLastGestureEndpointSeconds());
        IdleStartTime = Target->MeshComponent->GetPosition();
        Phase = 4; PhaseSeconds = 0;
        return;
    }
    if (Phase == 4 && PhaseSeconds >= .7f)
    {
        if (!OriginalIdlePlaying() || Target->GetInteractionCount() != Attempt + 1 ||
            FMath::IsNearlyEqual(IdleStartTime, Target->MeshComponent->GetPosition(), .01f))
        { Fail(TEXT("Original idle failed to advance after the gesture")); return; }
        for (const auto& Other : OtherCounts)
            if (!Other.Key.IsValid() || Other.Key->GetInteractionCount() != Other.Value)
            { Fail(TEXT("Shared F input affected another prop")); return; }
        if (Attempt == 0) { ++Attempt; BeginGesture(); return; }
        Current->SetBoolField(TEXT("original_idle_loop_resumed"), true);
        Current->SetBoolField(TEXT("holding_f_did_not_retrigger"), true);
        Current->SetBoolField(TEXT("unrelated_props_unchanged"), true);
        Current->SetBoolField(TEXT("same_player_throughout_input_check"), Player == TestedPlayer.Get());
        Current->SetBoolField(TEXT("no_automatic_area_travel"), Director->GetActiveArea() == TEXT("MainMap"));
        Current->SetNumberField(TEXT("manual_f_actions"), Target->GetInteractionCount());
        Current->SetNumberField(TEXT("completed_gestures"), Target->GetCompletedGestureCount());
        Current->SetBoolField(TEXT("passed"), true);
        Records.Add(MakeShared<FJsonValueObject>(Current));
        UE_LOG(LogTemp, Display, TEXT("REA_GESTURE_PROBE %s PASS"), *SourceId);
        ++Index; Phase = 0;
    }
}
