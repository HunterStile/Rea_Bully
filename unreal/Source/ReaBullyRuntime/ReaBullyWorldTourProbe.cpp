#include "ReaBullyWorldTourProbe.h"
#include "ReaBullyWorld.h"
#include "ReaBullyExploration.h"
#include "ReaBullyAmbient.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/Level.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/PlayerController.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "HAL/FileManager.h"
#include "InputKeyEventArgs.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UnrealClient.h"

AReaBullyWorldTourProbe::AReaBullyWorldTourProbe()
{
    PrimaryActorTick.bCanEverTick = true;
}

void AReaBullyWorldTourProbe::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    FScreenshotRequest::OnScreenshotRequestProcessed().Remove(CaptureDelegate);
    Super::EndPlay(EndPlayReason);
}

void AReaBullyWorldTourProbe::ScreenshotProcessed()
{
    bCaptureProcessed = true;
}

void AReaBullyWorldTourProbe::CompleteCheckpoint()
{
    Records.Add(MakeShared<FJsonValueObject>(Current));
    UE_LOG(LogTemp, Display, TEXT("REA_WORLD_TOUR checkpoint=%d %s walked=%.2f"), Index,
        Current->GetBoolField(TEXT("passed")) ? TEXT("PASS") : TEXT("FAIL"), Current->GetNumberField(TEXT("walked_cm")));
    ++Index;
    Phase = 0;
}

void AReaBullyWorldTourProbe::BeginAmbientSamples()
{
    AmbientSamples.Reset();
    for (TActorIterator<AReaBullyAmbientPed> It(GetWorld()); It; ++It)
    {
        if (!It->GetLevel() || !It->GetLevel()->bIsVisible) continue;
        FAmbientSample Sample;
        Sample.Ped = *It;
        Sample.LocalBones = It->GetMesh()->GetBoneSpaceTransforms();
        Sample.Position = It->GetActorLocation();
        AmbientSamples.Add(MoveTemp(Sample));
    }
}

void AReaBullyWorldTourProbe::RecordAmbientSamples()
{
    TArray<TSharedPtr<FJsonValue>> Samples;
    for (const FAmbientSample& Before : AmbientSamples)
    {
        auto Row = MakeShared<FJsonObject>();
        auto* Ped = Before.Ped.Get();
        Row->SetBoolField(TEXT("actor_survived_sample"), Ped != nullptr);
        if (Ped)
        {
            Row->SetStringField(TEXT("actor"), Ped->GetPathName());
            Row->SetNumberField(TEXT("ped_id"), Ped->Data ? Ped->Data->PedId : -1);
            Row->SetStringField(TEXT("data_asset"), GetPathNameSafe(Ped->Data));
            Row->SetStringField(TEXT("source_reference"), Ped->SourceReference);
            Row->SetBoolField(TEXT("visible"), !Ped->IsHidden());
            Row->SetBoolField(TEXT("own_skin_bound"), Ped->Data && Ped->Data->Mesh &&
                Ped->GetMesh()->GetSkeletalMeshAsset() == Ped->Data->Mesh);
            const auto* Movement = Ped->GetCharacterMovement();
            Row->SetBoolField(TEXT("grounded"), Movement->IsMovingOnGround());
            Row->SetBoolField(TEXT("grounded_on_recovered_geometry"),
                Cast<AReaBullyZoneGeometry>(Movement->CurrentFloor.HitResult.GetActor()) != nullptr);
            Row->SetStringField(TEXT("floor_component"), GetPathNameSafe(Movement->CurrentFloor.HitResult.GetComponent()));
            const TArray<FTransform>& After = Ped->GetMesh()->GetBoneSpaceTransforms();
            int32 Changed = 0;
            if (After.Num() == Before.LocalBones.Num())
                for (int32 Bone = 0; Bone < After.Num(); ++Bone)
                    Changed += !After[Bone].Equals(Before.LocalBones[Bone], 0.0001f);
            Row->SetNumberField(TEXT("bones_sampled"), Before.LocalBones.Num());
            Row->SetNumberField(TEXT("local_bones_changed"), Changed);
            Row->SetBoolField(TEXT("animation_changes_bones"), Changed > 0);
            Row->SetNumberField(TEXT("walked_cm_during_sample"), FVector::Dist2D(Before.Position, Ped->GetActorLocation()));
            Row->SetBoolField(TEXT("has_patrol_route"), Ped->Route.Num() > 1);
        }
        Samples.Add(MakeShared<FJsonValueObject>(Row));
    }
    Current->SetArrayField(TEXT("ambient_ped_samples"), Samples);
    Current->SetStringField(TEXT("ambient_sample_scope"), TEXT("Passive local-bone, visibility and floor observations during the short walk; not a full AI/route validation and not part of the checkpoint traversal pass."));
}

void AReaBullyWorldTourProbe::WalkKey(bool Pressed)
{
    if (auto* Controller = GetWorld()->GetFirstPlayerController())
        Controller->InputKey(FInputKeyEventArgs(nullptr, FInputDeviceId::CreateFromInternalId(0), EKeys::W,
            Pressed ? IE_Pressed : IE_Released, Pressed ? 1.0f : 0.0f, false, FPlatformTime::Cycles64()));
}

void AReaBullyWorldTourProbe::RecordFailure(const FString& Reason)
{
    WalkKey(false);
    if (Phase >= 3)
    {
        Current->SetBoolField(TEXT("screenshot_complete"), false);
        Current->SetStringField(TEXT("screenshot_error"), Reason);
    }
    if (auto* Controller = GetWorld()->GetFirstPlayerController())
    {
        if (auto* Player = Cast<AReaBullyExplorer>(Controller->GetPawn()))
        {
            Current->SetStringField(TEXT("player_position_at_failure"), Player->GetActorLocation().ToString());
            Current->SetNumberField(TEXT("movement_mode_at_failure"), Player->GetCharacterMovement()->MovementMode.GetValue());
            Current->SetStringField(TEXT("floor_component_at_failure"),
                GetPathNameSafe(Player->GetCharacterMovement()->CurrentFloor.HitResult.GetComponent()));
        }
    }
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It)
    {
        Current->SetStringField(TEXT("active_area_at_failure"), It->GetActiveArea().ToString());
        Current->SetBoolField(TEXT("director_ready_at_failure"), It->IsReady());
        TArray<TSharedPtr<FJsonValue>> Names;
        for (FName Name : It->VisibleZoneNames()) Names.Add(MakeShared<FJsonValueString>(Name.ToString()));
        Current->SetArrayField(TEXT("visible_zones_at_failure"), Names);
        break;
    }
    Current->SetBoolField(TEXT("passed"), false);
    Current->SetStringField(TEXT("error"), Reason);
    Records.Add(MakeShared<FJsonValueObject>(Current));
    UE_LOG(LogTemp, Warning, TEXT("REA_WORLD_TOUR checkpoint=%d FAIL %s"), Index, *Reason);
    ++Index;
    Phase = 0;
}

void AReaBullyWorldTourProbe::Finish(const FString& Error)
{
    WalkKey(false);
    FScreenshotRequest::OnScreenshotRequestProcessed().Remove(CaptureDelegate);
    SetActorTickEnabled(false);
    auto Report = MakeShared<FJsonObject>();
    bool Passed = Error.IsEmpty() && Records.Num() == Checkpoints.Num() && !Records.IsEmpty();
    for (const auto& Record : Records) Passed &= Record->AsObject()->GetBoolField(TEXT("passed"));
    Report->SetBoolField(TEXT("passed"), Passed);
    Report->SetStringField(TEXT("error"), Error);
    Report->SetStringField(TEXT("plan"), PlanPath);
    Report->SetStringField(TEXT("scope"), TEXT("Streaming, capsule floor and short keyboard walk at each sampled source position; not exhaustive area traversal."));
    if (!CaptureDirectory.IsEmpty())
    {
        Report->SetStringField(TEXT("capture_directory"), CaptureDirectory);
        Report->SetNumberField(TEXT("captures_expected"), Checkpoints.Num());
        Report->SetNumberField(TEXT("captures_completed"), Captured);
        Report->SetStringField(TEXT("capture_scope"), TEXT("Rendered PNG after the measured walk, with the original checkpoint heading. PNG completion is not a visual quality review."));
        Passed &= Captured == Checkpoints.Num();
        Report->SetBoolField(TEXT("passed"), Passed);
    }
    Report->SetArrayField(TEXT("checkpoints"), Records);
    FString Json;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Json));
    if (!FFileHelper::SaveStringToFile(Json, *ReportPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) Passed = false;
    UE_LOG(LogTemp, Display, TEXT("REA_WORLD_TOUR %s %s"), Passed ? TEXT("PASS") : TEXT("FAIL"), *ReportPath);
    FPlatformMisc::RequestExitWithStatus(false, Passed ? 0 : 1);
}

void AReaBullyWorldTourProbe::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bLoaded)
    {
        FParse::Value(FCommandLine::Get(), TEXT("ReaBullyTourCapture="), CaptureDirectory);
        if (!CaptureDirectory.IsEmpty())
        {
            CaptureDirectory = FPaths::ConvertRelativePathToFull(CaptureDirectory);
            if (FParse::Param(FCommandLine::Get(), TEXT("NullRHI")) ||
                !IFileManager::Get().DirectoryExists(*CaptureDirectory))
            { Finish(TEXT("Tour capture requires rendering and an existing new output directory")); return; }
            TArray<FString> Existing;
            IFileManager::Get().FindFiles(Existing, *(CaptureDirectory / TEXT("*")), true, true);
            if (!Existing.IsEmpty())
            { Finish(TEXT("Tour capture directory must be empty")); return; }
            CaptureDelegate = FScreenshotRequest::OnScreenshotRequestProcessed().AddUObject(
                this, &AReaBullyWorldTourProbe::ScreenshotProcessed);
        }
        FString Json;
        TSharedPtr<FJsonObject> Plan;
        const TArray<TSharedPtr<FJsonValue>>* Entries = nullptr;
        if (!FFileHelper::LoadFileToString(Json, *PlanPath) ||
            !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Plan) || !Plan.IsValid() ||
            !Plan->TryGetArrayField(TEXT("checkpoints"), Entries) || Entries->IsEmpty())
        { Finish(TEXT("Missing or invalid source tour plan")); return; }
        Checkpoints = *Entries;
        bLoaded = true;
        Deadline = FPlatformTime::Seconds() + 120;
    }
    auto* Controller = GetWorld()->GetFirstPlayerController();
    auto* Player = Controller ? Cast<AReaBullyExplorer>(Controller->GetPawn()) : nullptr;
    AReaBullyWorldDirector* Director = nullptr;
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It) { Director = *It; break; }
    if (!Player || !Director)
    {
        if (FPlatformTime::Seconds() > Deadline) Finish(TEXT("Player or world director missing"));
        return;
    }
    if (Index >= Checkpoints.Num()) { Finish(); return; }
    if (Phase == 0)
    {
        const auto& Source = Checkpoints[Index]->AsObject();
        FString Area, Name;
        const TArray<TSharedPtr<FJsonValue>>* Position = nullptr;
        if (!Source.IsValid() || !Source->TryGetStringField(TEXT("name"), Name) ||
            !Source->TryGetStringField(TEXT("area"), Area) || !Source->HasField(TEXT("source")) ||
            !Source->TryGetArrayField(TEXT("position_cm"), Position) || Position->Num() != 3)
        { Finish(TEXT("Malformed source checkpoint")); return; }
        FVector Feet;
        if (!(*Position)[0]->TryGetNumber(Feet.X) || !(*Position)[1]->TryGetNumber(Feet.Y) ||
            !(*Position)[2]->TryGetNumber(Feet.Z) || Feet.ContainsNaN())
        { Finish(TEXT("Nonfinite or nonnumeric checkpoint position")); return; }
        Current = MakeShared<FJsonObject>();
        Current->SetObjectField(TEXT("source_checkpoint"), Source);
        const FVector Destination = Feet + FVector(0, 0, Player->GetCapsuleComponent()->GetScaledCapsuleHalfHeight() + 2);
        Player->GetCharacterMovement()->StopMovementImmediately();
        double Yaw = 0;
        if (Source->HasField(TEXT("yaw_degrees")) &&
            (!Source->TryGetNumberField(TEXT("yaw_degrees"), Yaw) || !FMath::IsFinite(Yaw)))
        { Finish(TEXT("Nonfinite or nonnumeric checkpoint orientation")); return; }
        Controller->SetControlRotation(FRotator(0, Yaw, 0));
        Deadline = FPlatformTime::Seconds() + 120;
        if (!Director->RequestTravel(Destination, FName(*Area)))
        { RecordFailure(TEXT("Source area unavailable in world manifest")); return; }
        Phase = 1;
        return;
    }
    if (FPlatformTime::Seconds() > Deadline)
    {
        if (Phase >= 3)
        {
            // Never continue to another checkpoint with a screenshot request still outstanding.
            Current->SetBoolField(TEXT("screenshot_request_processed"), bCaptureProcessed);
            RecordFailure(Phase == 3 ? TEXT("Tour capture could not settle on recovered floor within 60 seconds") :
                TEXT("Tour screenshot did not produce a complete PNG within 60 seconds"));
            Finish(TEXT("Tour capture timed out; remaining checkpoints were not visited"));
        }
        else RecordFailure(TEXT("Streaming or capsule floor did not become ready"));
        return;
    }
    if (!Director->IsReady()) return;
    if (Phase == 1)
    {
        if (!Player->GetCharacterMovement()->IsMovingOnGround()) return;
        const auto& Floor = Player->GetCharacterMovement()->CurrentFloor.HitResult;
        Current->SetStringField(TEXT("floor_component"), GetPathNameSafe(Floor.GetComponent()));
        Current->SetBoolField(TEXT("grounded_on_recovered_geometry"), Cast<AReaBullyZoneGeometry>(Floor.GetActor()) != nullptr);
        Current->SetNumberField(TEXT("visible_zones"), Director->VisibleZones());
        TArray<TSharedPtr<FJsonValue>> Visible;
        const TArray<FName> Names = Director->VisibleZoneNames();
        for (FName Name : Names) Visible.Add(MakeShared<FJsonValueString>(Name.ToString()));
        Current->SetArrayField(TEXT("visible_zone_names"), Visible);
        FString ExpectedZone;
        const bool HasExpectedZone = Checkpoints[Index]->AsObject()->TryGetStringField(TEXT("zone"), ExpectedZone);
        Current->SetBoolField(TEXT("expected_zone_visible"), !HasExpectedZone || Names.Contains(FName(*ExpectedZone)));
        Current->SetStringField(TEXT("active_area"), Director->GetActiveArea().ToString());
        Current->SetBoolField(TEXT("active_area_matches_source"),
            Director->GetActiveArea() == FName(*Checkpoints[Index]->AsObject()->GetStringField(TEXT("area"))));
        WalkStart = Player->GetActorLocation();
        WalkPlayer = Player;
        Current->SetStringField(TEXT("player_position_at_walk_start"), WalkStart.ToString());
        WalkTime = 0;
        BeginAmbientSamples();
        WalkKey(true);
        Phase = 2;
    }
    else if (Phase == 2)
    {
        if (WalkPlayer.Get() != Player)
        { RecordFailure(TEXT("Player pawn was replaced during traversal (respawn/reset)")); return; }
        WalkTime += DeltaSeconds;
        if (WalkTime < 1) return;
        WalkKey(false);
        RecordAmbientSamples();
        const double Distance = FVector::Dist2D(WalkStart, Player->GetActorLocation());
        Current->SetNumberField(TEXT("walked_cm"), Distance);
        Current->SetStringField(TEXT("player_position_after_walk"), Player->GetActorLocation().ToString());
        Current->SetBoolField(TEXT("grounded_after_walk"), Player->GetCharacterMovement()->IsMovingOnGround());
        Current->SetBoolField(TEXT("grounded_after_walk_on_recovered_geometry"),
            Cast<AReaBullyZoneGeometry>(Player->GetCharacterMovement()->CurrentFloor.HitResult.GetActor()) != nullptr);
        Current->SetStringField(TEXT("floor_component_after_walk"),
            GetPathNameSafe(Player->GetCharacterMovement()->CurrentFloor.HitResult.GetComponent()));
        FString ExpectedZoneAfter;
        const bool HasExpectedZoneAfter = Checkpoints[Index]->AsObject()->TryGetStringField(TEXT("zone"), ExpectedZoneAfter);
        const bool AreaMatchesAfter = Director->GetActiveArea() == FName(*Checkpoints[Index]->AsObject()->GetStringField(TEXT("area")));
        const bool ZoneVisibleAfter = !HasExpectedZoneAfter || Director->VisibleZoneNames().Contains(FName(*ExpectedZoneAfter));
        Current->SetBoolField(TEXT("same_player_after_walk"), WalkPlayer.Get() == Player);
        Current->SetBoolField(TEXT("active_area_matches_source_after_walk"), AreaMatchesAfter);
        Current->SetBoolField(TEXT("expected_zone_visible_after_walk"), ZoneVisibleAfter);
        const bool Passed = Current->GetBoolField(TEXT("grounded_on_recovered_geometry")) &&
            Current->GetBoolField(TEXT("expected_zone_visible")) &&
            Current->GetBoolField(TEXT("active_area_matches_source")) &&
            Current->GetBoolField(TEXT("grounded_after_walk_on_recovered_geometry")) &&
            Player->GetCharacterMovement()->IsMovingOnGround() && Distance > 50 &&
            AreaMatchesAfter && ZoneVisibleAfter;
        Current->SetBoolField(TEXT("passed"), Passed);
        if (CaptureDirectory.IsEmpty()) { CompleteCheckpoint(); return; }
        Current->SetBoolField(TEXT("traversal_passed_before_capture"), Passed);
        Current->SetBoolField(TEXT("screenshot_complete"), false);
        CapturePath = CaptureDirectory / FString::Printf(TEXT("checkpoint-%03d.png"), Index);
        Current->SetStringField(TEXT("screenshot_path"), CapturePath);
        if (IFileManager::Get().FileExists(*CapturePath))
        { RecordFailure(TEXT("Tour screenshot output already exists")); Finish(TEXT("New screenshot output required")); return; }
        Player->GetCharacterMovement()->StopMovementImmediately();
        CaptureSettleTime = 0;
        CaptureSettleFrames = 0;
        bCaptureProcessed = false;
        Deadline = FPlatformTime::Seconds() + 60;
        Phase = 3;
    }
    else if (Phase == 3 || Phase == 4)
    {
        FString ExpectedZone;
        const auto& Source = Checkpoints[Index]->AsObject();
        const bool HasExpectedZone = Source->TryGetStringField(TEXT("zone"), ExpectedZone);
        const auto* Movement = Player->GetCharacterMovement();
        const bool Stable = WalkPlayer.Get() == Player && Movement->IsMovingOnGround() &&
            Cast<AReaBullyZoneGeometry>(Movement->CurrentFloor.HitResult.GetActor()) != nullptr &&
            Director->GetActiveArea() == FName(*Source->GetStringField(TEXT("area"))) &&
            (!HasExpectedZone || Director->VisibleZoneNames().Contains(FName(*ExpectedZone))) &&
            Player->GetVelocity().SizeSquared() < 1;
        if (WalkPlayer.Get() != Player)
        { RecordFailure(TEXT("Player pawn was replaced during tour capture")); Finish(TEXT("Capture pawn changed")); return; }
        if (Phase == 3)
        {
            CaptureSettleTime = Stable ? CaptureSettleTime + DeltaSeconds : 0;
            CaptureSettleFrames = Stable ? CaptureSettleFrames + 1 : 0;
            if (CaptureSettleTime < 1 || CaptureSettleFrames < 30 || FScreenshotRequest::IsScreenshotRequested()) return;
            Current->SetStringField(TEXT("player_position_at_capture"), Player->GetActorLocation().ToString());
            Current->SetStringField(TEXT("camera_rotation_at_capture"), Controller->GetControlRotation().ToString());
            Current->SetStringField(TEXT("floor_component_at_capture"), GetPathNameSafe(Movement->CurrentFloor.HitResult.GetComponent()));
            Current->SetBoolField(TEXT("capture_same_player_area_zone_recovered_floor_stationary"), Stable);
            bCaptureProcessed = false;
            FScreenshotRequest::RequestScreenshot(CapturePath, true, false, false);
            UE_LOG(LogTemp, Display, TEXT("REA_WORLD_TOUR_CAPTURE checkpoint=%d REQUEST %s"), Index, *CapturePath);
            Phase = 4;
            Deadline = FPlatformTime::Seconds() + 60;
            return;
        }
        if (!Stable)
        { RecordFailure(TEXT("Player, area or recovered floor changed while saving tour screenshot")); Finish(TEXT("Capture state changed")); return; }
        if (!bCaptureProcessed || FScreenshotRequest::IsScreenshotRequested()) return;
        TArray<uint8> Png;
        // The engine broadcasts completion after synchronous SaveImageByExtension. Check its
        // PNG header and final IEND too, since the callback also fires when saving fails.
        static const uint8 Signature[] = {137, 80, 78, 71, 13, 10, 26, 10};
        static const uint8 Header[] = {0, 0, 0, 13, 73, 72, 68, 82};
        static const uint8 End[] = {0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96, 130};
        if (!FFileHelper::LoadFileToArray(Png, *CapturePath, FILEREAD_Silent) || Png.Num() < 45 ||
            FMemory::Memcmp(Png.GetData(), Signature, sizeof(Signature)) != 0 ||
            FMemory::Memcmp(Png.GetData() + 8, Header, sizeof(Header)) != 0 ||
            FMemory::Memcmp(Png.GetData() + Png.Num() - sizeof(End), End, sizeof(End)) != 0) return;
        const uint32 Width = (uint32(Png[16]) << 24) | (uint32(Png[17]) << 16) | (uint32(Png[18]) << 8) | Png[19];
        const uint32 Height = (uint32(Png[20]) << 24) | (uint32(Png[21]) << 16) | (uint32(Png[22]) << 8) | Png[23];
        if (Width == 0 || Height == 0) return;
        Current->SetBoolField(TEXT("screenshot_request_processed"), true);
        Current->SetBoolField(TEXT("screenshot_complete"), true);
        Current->SetNumberField(TEXT("screenshot_bytes"), Png.Num());
        Current->SetNumberField(TEXT("screenshot_width"), Width);
        Current->SetNumberField(TEXT("screenshot_height"), Height);
        ++Captured;
        CompleteCheckpoint();
    }
}
