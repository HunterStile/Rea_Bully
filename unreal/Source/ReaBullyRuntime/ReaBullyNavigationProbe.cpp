#include "ReaBullyNavigationProbe.h"
#include "ReaBullyAreaNavigation.h"
#include "ReaBullyExploration.h"
#include "ReaBullyWorld.h"
#include "Components/CapsuleComponent.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/PlayerController.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformTime.h"
#include "InputKeyEventArgs.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

AReaBullyNavigationProbe::AReaBullyNavigationProbe()
{
    PrimaryActorTick.bCanEverTick = true;
    Deadline = FPlatformTime::Seconds() + 120;
    Report = MakeShared<FJsonObject>();
}

void AReaBullyNavigationProbe::Key(const FKey& Input, bool Pressed)
{
    if (auto* Controller = GetWorld()->GetFirstPlayerController())
        Controller->InputKey(FInputKeyEventArgs(nullptr, FInputDeviceId::CreateFromInternalId(0), Input,
            Pressed ? IE_Pressed : IE_Released, Pressed ? 1.0f : 0.0f, false, FPlatformTime::Cycles64()));
}

void AReaBullyNavigationProbe::Tap(const FKey& Input)
{
    Key(Input, true);
    Key(Input, false);
}

void AReaBullyNavigationProbe::Finish(const FString& Error)
{
    Key(EKeys::W, false);
    SetActorTickEnabled(false);
    bool Passed = Error.IsEmpty() && TravelRecords.Num() == 2;
    Report->SetBoolField(TEXT("passed"), Passed);
    Report->SetStringField(TEXT("error"), Error);
    Report->SetStringField(TEXT("scope"), TEXT("Real keyboard menu focus/cancel and two selected area arrivals; complete traversal coverage is recorded separately."));
    Report->SetArrayField(TEXT("travels"), TravelRecords);
    FString Json;
    FJsonSerializer::Serialize(Report.ToSharedRef(), TJsonWriterFactory<>::Create(&Json));
    if (!FFileHelper::SaveStringToFile(Json, *ReportPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) Passed = false;
    UE_LOG(LogTemp, Display, TEXT("REA_NAVIGATION_PROBE %s %s"), Passed ? TEXT("PASS") : TEXT("FAIL"), *ReportPath);
    FPlatformMisc::RequestExitWithStatus(false, Passed ? 0 : 1);
}

void AReaBullyNavigationProbe::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (FPlatformTime::Seconds() > Deadline) { Finish(TEXT("Menu or streamed arrival timed out")); return; }
    auto* Controller = GetWorld()->GetFirstPlayerController();
    auto* Player = Controller ? Cast<AReaBullyExplorer>(Controller->GetPawn()) : nullptr;
    AReaBullyAreaNavigation* Menu = nullptr;
    AReaBullyWorldDirector* Director = nullptr;
    for (TActorIterator<AReaBullyAreaNavigation> It(GetWorld()); It; ++It) { Menu = *It; break; }
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It) { Director = *It; break; }
    if (!Player || !Menu || !Director) return;
    if (Phase > 0 && TestedPlayer.Get() != Player)
    { Finish(TEXT("The player respawned during the navigation check")); return; }
    if (Phase == 0)
    {
        if (!Director->IsReady() || !Player->GetCharacterMovement()->IsMovingOnGround()) return;
        if (!Menu->IsAvailable() || Menu->EntryCount() < 2)
        { Finish(TEXT("At least two traversal-verified menu entries are required")); return; }
        Report->SetNumberField(TEXT("available_entries"), Menu->EntryCount());
        TestedPlayer = Player;
        StartPosition = Player->GetActorLocation();
        StartRotation = Controller->GetControlRotation();
        Tap(EKeys::Tab);
        const bool Focus = Menu->IsOpen() && Controller->IsMoveInputIgnored() && Controller->IsLookInputIgnored();
        Report->SetBoolField(TEXT("tab_opened_and_locked_gameplay"), Focus);
        if (!Focus) { Finish(TEXT("Tab did not open the menu and capture gameplay input")); return; }
        Key(EKeys::W, true);
        Tap(EKeys::E);
        Tap(EKeys::F);
        Tap(EKeys::SpaceBar);
        Controller->InputKey(FInputKeyEventArgs(nullptr, FInputDeviceId::CreateFromInternalId(0), EKeys::MouseX,
            10.0f, 1.0f / 60.0f, 1, FPlatformTime::Cycles64()));
        PhaseSeconds = 0;
        Phase = 1;
        return;
    }
    if (Phase == 1)
    {
        PhaseSeconds += DeltaSeconds;
        if (PhaseSeconds < .6f) return;
        const double Distance = FVector::Dist(StartPosition, Player->GetActorLocation());
        const bool Suppressed = Distance < 1 && Controller->GetControlRotation().Equals(StartRotation, .01f) &&
            !Controller->IsInputKeyDown(EKeys::W) && Player->GetCharacterMovement()->IsMovingOnGround();
        Report->SetNumberField(TEXT("distance_while_menu_open_cm"), Distance);
        Report->SetBoolField(TEXT("movement_look_and_jump_suppressed"), Suppressed);
        if (!Suppressed) { Finish(TEXT("Gameplay input leaked while the area menu was open")); return; }
        Key(EKeys::W, false);
        Tap(EKeys::Escape);
        const bool Closed = !Menu->IsOpen() && !Controller->IsMoveInputIgnored() && !Controller->IsLookInputIgnored();
        Report->SetBoolField(TEXT("escape_closed_and_restored_inputs"), Closed);
        if (!Closed) { Finish(TEXT("Escape did not cancel the menu cleanly")); return; }
        StartPosition = Player->GetActorLocation();
        PhaseSeconds = 0;
        Key(EKeys::W, true);
        Phase = 2;
        return;
    }
    if (Phase == 2)
    {
        PhaseSeconds += DeltaSeconds;
        if (PhaseSeconds < 1) return;
        Key(EKeys::W, false);
        const double Distance = FVector::Dist2D(StartPosition, Player->GetActorLocation());
        Report->SetNumberField(TEXT("walked_after_escape_cm"), Distance);
        const bool Restored = Distance > 50 && Player->GetCharacterMovement()->IsMovingOnGround();
        Report->SetBoolField(TEXT("game_continued_and_walk_restored"), Restored);
        if (!Restored) { Finish(TEXT("Walking did not resume after cancelling the menu")); return; }
        Tap(EKeys::Tab);
        Phase = 3;
        return;
    }
    if (Phase == 3)
    {
        if (!Menu->IsOpen() || !Menu->SelectedEntry())
        { Finish(TEXT("Could not reopen the menu")); return; }
        const auto& Entry = *Menu->SelectedEntry();
        SelectedId = Entry.SourceId;
        SelectedArea = Entry.Area;
        SelectedFeet = Entry.PositionFeet;
        Tap(EKeys::Enter);
        if (Menu->IsOpen()) { Finish(TEXT("Enter did not dispatch the selected destination")); return; }
        Deadline = FPlatformTime::Seconds() + 120;
        Phase = 4;
        return;
    }
    if (Phase == 4)
    {
        if (!Director->IsReady() || !Player->GetCharacterMovement()->IsMovingOnGround()) return;
        const auto& Floor = Player->GetCharacterMovement()->CurrentFloor.HitResult;
        const double Distance = FVector::Dist2D(Player->GetActorLocation(), SelectedFeet);
        const bool Passed = Director->GetActiveArea() == SelectedArea && Distance < 200 &&
            Cast<AReaBullyZoneGeometry>(Floor.GetActor()) != nullptr &&
            !Controller->IsMoveInputIgnored() && !Controller->IsLookInputIgnored();
        auto Row = MakeShared<FJsonObject>();
        Row->SetStringField(TEXT("source_id"), SelectedId);
        Row->SetStringField(TEXT("selected_area"), SelectedArea.ToString());
        Row->SetStringField(TEXT("actual_area"), Director->GetActiveArea().ToString());
        Row->SetStringField(TEXT("floor_component"), GetPathNameSafe(Floor.GetComponent()));
        Row->SetNumberField(TEXT("arrival_horizontal_error_cm"), Distance);
        Row->SetBoolField(TEXT("passed"), Passed);
        TravelRecords.Add(MakeShared<FJsonValueObject>(Row));
        if (!Passed) { Finish(TEXT("Selected arrival did not reach its area and recovered floor")); return; }
        Tap(EKeys::Tab);
        if (!Menu->IsOpen()) { Finish(TEXT("Menu did not reopen after arrival")); return; }
        if (TravelRecords.Num() == 1)
        {
            Tap(EKeys::Down);
            if (!Menu->SelectedEntry() || Menu->SelectedEntry()->Area == SelectedArea)
            { Finish(TEXT("Down did not select a different area")); return; }
            Phase = 3;
        }
        else
        {
            Tap(EKeys::Escape);
            const bool Closed = !Menu->IsOpen() && !Controller->IsMoveInputIgnored() && !Controller->IsLookInputIgnored();
            Report->SetBoolField(TEXT("reopen_and_cancel_after_travel"), Closed);
            Report->SetBoolField(TEXT("same_player_throughout_check"), TestedPlayer.Get() == Player);
            Finish(Closed ? TEXT("") : TEXT("Input locks were left after repeated menu use"));
        }
    }
}
