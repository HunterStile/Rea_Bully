#include "ReaBullyPropInteractionProbe.h"
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
bool ReadVector(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field, FVector& Value)
{
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    return Object.IsValid() && Object->TryGetArrayField(Field, Values) && Values->Num() == 3 &&
        (*Values)[0]->TryGetNumber(Value.X) && (*Values)[1]->TryGetNumber(Value.Y) &&
        (*Values)[2]->TryGetNumber(Value.Z) && !Value.ContainsNaN();
}
}

AReaBullyPropInteractionProbe::AReaBullyPropInteractionProbe()
{
    PrimaryActorTick.bCanEverTick = true;
}

void AReaBullyPropInteractionProbe::Key(bool Pressed)
{
    bKeyHeld = Pressed;
    KeyFrame = GFrameCounter;
    if (auto* Controller = GetWorld()->GetFirstPlayerController())
        Controller->InputKey(FInputKeyEventArgs(nullptr, FInputDeviceId::CreateFromInternalId(0), EKeys::F,
            Pressed ? IE_Pressed : IE_Released, Pressed ? 1.0f : 0.0f, false, FPlatformTime::Cycles64()));
}

bool AReaBullyPropInteractionProbe::RequestApproach(AReaBullyWorldDirector* Director, AReaBullyExplorer* Player)
{
    // Diagnostic approach only: 160 cm off the thin horizontal axis of the
    // original closed skin box. This is not an original spawn or trigger point.
    FVector Local = ClosedCenter;
    Local.Z -= ClosedExtent.Z;
    const double Offset = Approach == 0 ? -160.0 : 160.0;
    if (ClosedExtent.X < ClosedExtent.Y) Local.X += Offset;
    else Local.Y += Offset;
    const FVector Feet = SourceTransform.TransformPosition(Local);
    Current->SetNumberField(TEXT("diagnostic_approach_side"), Approach);
    Current->SetStringField(TEXT("diagnostic_approach_feet_cm"), Feet.ToString());
    Deadline = FPlatformTime::Seconds() + 90;
    PhaseSeconds = 0;
    Player->GetCharacterMovement()->StopMovementImmediately();
    return Director->RequestTravel(Feet + FVector(0, 0,
        Player->GetCapsuleComponent()->GetScaledCapsuleHalfHeight() + 2), Area);
}

TArray<FTransform> AReaBullyPropInteractionProbe::ReadBones() const
{
    TArray<FTransform> Result;
    if (Target.IsValid())
        for (int32 Bone = 0; Bone < Target->MeshComponent->GetNumBones(); ++Bone)
            Result.Add(Target->MeshComponent->GetBoneTransform(Bone));
    return Result;
}

bool AReaBullyPropInteractionProbe::SameBones(const TArray<FTransform>& Previous) const
{
    const auto Now = ReadBones();
    if (Now.IsEmpty() || Now.Num() != Previous.Num()) return false;
    for (int32 IndexBone = 0; IndexBone < Now.Num(); ++IndexBone)
        if (!Now[IndexBone].Equals(Previous[IndexBone], 0.01f)) return false;
    return true;
}

bool AReaBullyPropInteractionProbe::AtClipEnd(UAnimSequence* Clip) const
{
    auto* Instance = Target.IsValid() ? Target->MeshComponent->GetSingleNodeInstance() : nullptr;
    return Clip && Instance && Instance->GetCurrentAsset() == Clip &&
        FMath::IsNearlyEqual(Target->MeshComponent->GetPosition(), Clip->GetPlayLength(), .001f);
}

void AReaBullyPropInteractionProbe::Fail(const FString& Reason)
{
    Key(false);
    Current->SetBoolField(TEXT("passed"), false);
    Current->SetStringField(TEXT("error"), Reason);
    Current->SetNumberField(TEXT("phase_at_failure"), Phase);
    if (Target.IsValid())
    {
        Current->SetNumberField(TEXT("interaction_count_at_failure"), Target->GetInteractionCount());
        Current->SetBoolField(TEXT("moving_at_failure"), Target->IsInteractionMoving());
        Current->SetBoolField(TEXT("open_at_failure"), Target->IsInteractionOpen());
    }
    Records.Add(MakeShared<FJsonValueObject>(Current));
    UE_LOG(LogTemp, Warning, TEXT("REA_GARAGE_PROBE %s FAIL %s"), *SourceId, *Reason);
    ++Index; Phase = 0;
}

void AReaBullyPropInteractionProbe::Finish(const FString& Error)
{
    Key(false);
    SetActorTickEnabled(false);
    bool Passed = Error.IsEmpty() && Placements.Num() == 2 && Records.Num() == 2;
    for (const auto& Record : Records) Passed &= Record->AsObject()->GetBoolField(TEXT("passed"));
    auto Report = MakeShared<FJsonObject>();
    Report->SetBoolField(TEXT("passed"), Passed);
    Report->SetStringField(TEXT("error"), Error);
    Report->SetStringField(TEXT("plan"), PlanPath);
    Report->SetStringField(TEXT("scope"), TEXT("Two DAT garages: diagnostic approach, real F input, original OPEN/CLOSE endpoints, stable held poses and no automatic area travel. No bicycle gameplay or collision claim."));
    Report->SetArrayField(TEXT("placements"), Records);
    FString Json;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Json));
    if (!FFileHelper::SaveStringToFile(Json, *ReportPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) Passed = false;
    FPlatformMisc::RequestExitWithStatus(false, Passed ? 0 : 1);
}

void AReaBullyPropInteractionProbe::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (bKeyHeld && GFrameCounter > KeyFrame + 1) Key(false);
    if (!bLoaded)
    {
        FString Json;
        TSharedPtr<FJsonObject> Plan;
        const TArray<TSharedPtr<FJsonValue>>* Rows = nullptr;
        const TSharedPtr<FJsonObject>* Model = nullptr;
        const TSharedPtr<FJsonObject>* Bounds = nullptr;
        if (!FFileHelper::LoadFileToString(Json, *PlanPath) ||
            !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Plan) || !Plan.IsValid() ||
            !Plan->TryGetArrayField(TEXT("placements"), Rows) || Rows->Num() != 2 ||
            !Plan->TryGetObjectField(TEXT("model"), Model) || !(*Model)->TryGetObjectField(TEXT("closed_bounds_cm"), Bounds) ||
            !(*Model)->TryGetStringField(TEXT("source_sha256"), SourceDigest) ||
            !ReadVector(*Bounds, TEXT("center"), ClosedCenter) || !ReadVector(*Bounds, TEXT("extent"), ClosedExtent) ||
            ClosedExtent.GetMin() <= 0)
        { Finish(TEXT("Invalid verified garage interaction plan")); return; }
        Placements = *Rows;
        bLoaded = true;
        Deadline = FPlatformTime::Seconds() + 120;
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
    if (Phase >= 2 && Player != TestedPlayer.Get())
    { Fail(TEXT("Player respawned during garage input verification")); return; }
    if (Phase == 0)
    {
        const auto Row = Placements[Index]->AsObject();
        const TSharedPtr<FJsonObject>* Transform = nullptr;
        const TArray<TSharedPtr<FJsonValue>>* Quaternion = nullptr;
        FVector Translation, Scale;
        FQuat Rotation;
        FString AreaName, ZoneName;
        if (!Row.IsValid() || !Row->TryGetStringField(TEXT("source_id"), SourceId) ||
            SourceId != (Index == 0 ? TEXT("tbusines_doors.dat:23") : TEXT("tBMX.dat:23")) ||
            !Row->TryGetStringField(TEXT("area"), AreaName) || !Row->TryGetStringField(TEXT("zone"), ZoneName) ||
            !Row->TryGetObjectField(TEXT("transform"), Transform) ||
            !ReadVector(*Transform, TEXT("translation_cm"), Translation) || !ReadVector(*Transform, TEXT("scale_xyz"), Scale) ||
            !(*Transform)->TryGetArrayField(TEXT("rotation_quaternion_xyzw"), Quaternion) || Quaternion->Num() != 4 ||
            !(*Quaternion)[0]->TryGetNumber(Rotation.X) || !(*Quaternion)[1]->TryGetNumber(Rotation.Y) ||
            !(*Quaternion)[2]->TryGetNumber(Rotation.Z) || !(*Quaternion)[3]->TryGetNumber(Rotation.W) ||
            Rotation.ContainsNaN() || !Rotation.IsNormalized())
        { Finish(TEXT("Malformed source garage placement")); return; }
        Area = FName(*AreaName); Zone = FName(*ZoneName);
        SourceTransform = FTransform(Rotation, Translation, Scale);
        Current = MakeShared<FJsonObject>();
        Current->SetObjectField(TEXT("source_placement"), Row);
        Current->SetStringField(TEXT("approach_policy"), TEXT("Diagnostic source closed-box horizontal normal +/-160cm; feet at original closed-box minimum Z. Not an original gameplay spawn."));
        Target.Reset(); OtherCounts.Reset(); Approach = 0;
        if (!RequestApproach(Director, Player)) { Fail(TEXT("Source area unavailable")); return; }
        Phase = 1;
        return;
    }
    if (FPlatformTime::Seconds() > Deadline)
    {
        if (Phase == 1 && Approach == 0)
        {
            Approach = 1;
            Current->SetBoolField(TEXT("first_approach_timed_out"), true);
            if (RequestApproach(Director, Player)) return;
        }
        Fail(TEXT("Garage phase timed out")); return;
    }
    if (!Director->IsReady()) return;
    if (Director->GetActiveArea() != Area) { Fail(TEXT("Unexpected area transition")); return; }
    PhaseSeconds += DeltaSeconds;
    if (Phase == 1)
    {
        if (!Player->GetCharacterMovement()->IsMovingOnGround()) return;
        if (!Director->VisibleZoneNames().Contains(Zone)) { Fail(TEXT("Source garage zone not visible")); return; }
        int32 Matches = 0;
        for (TActorIterator<AReaBullyWorldProp> It(GetWorld()); It; ++It)
            if (It->GetLevel() && It->GetLevel()->bIsVisible)
            {
                if (It->InteractionSourceId == SourceId) { Target = *It; ++Matches; }
                else OtherCounts.Add(*It, It->GetInteractionCount());
            }
        if (Matches != 1 || !Target.IsValid() || Target->IsHidden() || !Target->Data || !Target->InteractionData ||
            Target->Data->SourceSHA256 != SourceDigest || Target->InteractionData->SourceSHA256 != SourceDigest ||
            !Target->GetActorTransform().Equals(SourceTransform, .01f) || !Target->bExplorationInteractionEnabled)
        { Fail(TEXT("Exactly one configured original garage at its source transform required")); return; }
        if (!Cast<AReaBullyZoneGeometry>(Player->GetCharacterMovement()->CurrentFloor.HitResult.GetActor()))
        { Fail(TEXT("Diagnostic approach is not grounded on recovered geometry")); return; }
        Controller->SetControlRotation((Target->GetInteractionWorldLocation() - Player->GetActorLocation()).Rotation());
        Current->SetStringField(TEXT("player_position_cm"), Player->GetActorLocation().ToString());
        Current->SetStringField(TEXT("floor_component"), GetPathNameSafe(Player->GetCharacterMovement()->CurrentFloor.HitResult.GetComponent()));
        ClosedBones = ReadBones();
        TestedPlayer = Player;
        Phase = 2; PhaseSeconds = 0; Deadline = FPlatformTime::Seconds() + 30;
        return;
    }
    if (!Target.IsValid()) { Fail(TEXT("Garage unloaded during interaction")); return; }
    if (Phase == 2 && PhaseSeconds > .6f)
    {
        if (Target->GetInteractionCount() != 0 || Target->IsInteractionMoving() || Target->IsInteractionOpen() ||
            !SameBones(ClosedBones)) { Fail(TEXT("Garage changed before manual input")); return; }
        Current->SetBoolField(TEXT("no_autoplay_before_input"), true);
        Key(true); bSawMoving = false; Phase = 3; PhaseSeconds = 0;
        return;
    }
    if (Phase == 3 || Phase == 5)
    {
        const bool Opening = Phase == 3;
        const int32 Count = Opening ? 1 : 2;
        bSawMoving |= Target->IsInteractionMoving();
        if (Target->GetInteractionCount() > Count) { Fail(TEXT("One F press produced repeated actions")); return; }
        if (Target->GetInteractionCount() != Count || Target->IsInteractionMoving()) return;
        auto* Clip = Opening ? Target->InteractionData->OpenClip.Get() : Target->InteractionData->CloseClip.Get();
        if (Target->IsInteractionOpen() != Opening || !bSawMoving || !AtClipEnd(Clip) ||
            SameBones(Opening ? ClosedBones : OpenBones))
        { Fail(TEXT("F did not complete a changing original one-shot clip")); return; }
        Current->SetBoolField(Opening ? TEXT("f_open_completed") : TEXT("f_close_completed"), true);
        Current->SetStringField(Opening ? TEXT("open_clip") : TEXT("close_clip"), Clip->GetPathName());
        Current->SetNumberField(Opening ? TEXT("open_endpoint_seconds") : TEXT("close_endpoint_seconds"), Target->MeshComponent->GetPosition());
        if (Opening) OpenBones = ReadBones(); else FinalBones = ReadBones();
        ++Phase; PhaseSeconds = 0; Deadline = FPlatformTime::Seconds() + 30;
        return;
    }
    if ((Phase == 4 || Phase == 6) && PhaseSeconds > .6f)
    {
        const bool Opening = Phase == 4;
        auto* Clip = Opening ? Target->InteractionData->OpenClip.Get() : Target->InteractionData->CloseClip.Get();
        if (Target->IsInteractionMoving() || Target->IsInteractionOpen() != Opening ||
            Target->GetInteractionCount() != (Opening ? 1 : 2) || !AtClipEnd(Clip) ||
            !SameBones(Opening ? OpenBones : FinalBones))
        { Fail(TEXT("Original clip endpoint did not remain stable")); return; }
        Current->SetBoolField(Opening ? TEXT("open_endpoint_stable") : TEXT("closed_endpoint_stable"), true);
        if (Opening)
        {
            FString Screenshot;
            if (Index == 0 && FParse::Value(FCommandLine::Get(), TEXT("ReaBullyScreenshot="), Screenshot))
                FScreenshotRequest::RequestScreenshot(Screenshot, true, false);
            Key(true); bSawMoving = false; Phase = 5; PhaseSeconds = 0;
            return;
        }
        for (const auto& Other : OtherCounts)
            if (!Other.Key.IsValid() || Other.Key->GetInteractionCount() != Other.Value)
            { Fail(TEXT("An unrelated prop changed from the same input")); return; }
        Current->SetBoolField(TEXT("unrelated_props_unchanged"), true);
        Current->SetBoolField(TEXT("no_automatic_area_travel"), Director->GetActiveArea() == Area);
        Current->SetBoolField(TEXT("closed_pose_matches_initial_idle"), SameBones(ClosedBones));
        Current->SetNumberField(TEXT("f_action_count"), Target->GetInteractionCount());
        Current->SetBoolField(TEXT("same_player_throughout_input_check"), Player == TestedPlayer.Get());
        Current->SetBoolField(TEXT("passed"), true);
        Records.Add(MakeShared<FJsonValueObject>(Current));
        UE_LOG(LogTemp, Display, TEXT("REA_GARAGE_PROBE %s PASS"), *SourceId);
        ++Index; Phase = 0;
    }
}
