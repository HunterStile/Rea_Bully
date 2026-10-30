#include "ReaBullyAreaNavigation.h"
#include "ReaBullyWorld.h"
#include "Components/CapsuleComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/HUD.h"
#include "GameFramework/PlayerController.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMisc.h"
#include "InputKeyEventArgs.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include <openssl/sha.h>

namespace
{
bool FileHasHash(const FString& Path, const FString& Expected)
{
    if (Expected.Len() != 64) return false;
    TArray<uint8> Bytes;
    FSHA256Signature Signature;
    return FFileHelper::LoadFileToArray(Bytes, *Path) &&
        SHA256(Bytes.GetData(), Bytes.Num(), Signature.Signature) &&
        Signature.ToString().Equals(Expected, ESearchCase::IgnoreCase);
}
}

AReaBullyAreaNavigation::AReaBullyAreaNavigation()
{
    PrimaryActorTick.bCanEverTick = false;
}

void AReaBullyAreaNavigation::BeginPlay()
{
    Super::BeginPlay();
    const TCHAR* Asset = TEXT("/Game/World/Navigation/DA_AreaNavigation.DA_AreaNavigation");
    if (FPackageName::DoesPackageExist(TEXT("/Game/World/Navigation/DA_AreaNavigation")))
        Data = LoadObject<UReaBullyAreaNavigationData>(nullptr, Asset, nullptr, LOAD_NoWarn);
    bVerified = VerifySavedScene();
    UE_LOG(LogTemp, Display, TEXT("REA_AREA_NAVIGATION %s entries=%d"),
        bVerified ? TEXT("AVAILABLE") : TEXT("UNAVAILABLE"), bVerified ? Data->Entries.Num() : 0);
}

bool AReaBullyAreaNavigation::VerifySavedScene() const
{
    if (!Data || Data->Entries.IsEmpty() || Data->EvidenceSHA256.Len() != 64 ||
        !FileHasHash(FModuleManager::Get().GetModuleFilename(TEXT("ReaBullyRuntime")), Data->RuntimeSHA256)) return false;
    TSet<FString> Required;
    Required.Add(TEXT("unreal/Content/Maps/WorldExploration.umap"));
    Required.Add(TEXT("unreal/Content/World/DA_World.uasset"));
    const FString ZonePrefix = TEXT("unreal/Content/World/Zones/");
    TArray<FString> ZoneFiles;
    IFileManager::Get().FindFiles(ZoneFiles, *(FPaths::ProjectContentDir() / TEXT("World/Zones/*.umap")), true, false);
    if (ZoneFiles.IsEmpty()) return false;
    for (const FString& Name : ZoneFiles) Required.Add(ZonePrefix + Name);
    if (Required.Num() != Data->SceneAssetHashes.Num()) return false;
    for (const FString& Relative : Required)
    {
        const FString* Digest = Data->SceneAssetHashes.Find(Relative);
        // Required contains only enumerated local scene assets, never arbitrary paths from the data asset.
        if (!Digest || !FileHasHash(FPaths::ProjectDir() / Relative.RightChop(7), *Digest)) return false;
    }
    TSet<FName> Areas;
    TSet<FString> Ids;
    for (const auto& Entry : Data->Entries)
    {
        if (Entry.SourceId.IsEmpty() || Entry.Area.IsNone() || Entry.DisplayName.IsEmpty() ||
            Entry.PositionFeet.ContainsNaN() || !FMath::IsFinite(Entry.YawDegrees) ||
            Areas.Contains(Entry.Area) || Ids.Contains(Entry.SourceId)) return false;
        Areas.Add(Entry.Area);
        Ids.Add(Entry.SourceId);
    }
    return true;
}

void AReaBullyAreaNavigation::SetOpen(bool Open)
{
    if (bOpen == Open) return;
    if (Open)
    {
        APlayerController* Controller = GetWorld()->GetFirstPlayerController();
        if (!Controller || !bVerified) return;
        Controller->FlushPressedKeys();
        Controller->SetIgnoreMoveInput(true);
        Controller->SetIgnoreLookInput(true);
        if (auto* Character = Cast<ACharacter>(Controller->GetPawn()))
            Character->GetCharacterMovement()->StopMovementImmediately();
        LockedController = Controller;
    }
    else if (APlayerController* Controller = LockedController.Get())
    {
        Controller->FlushPressedKeys();
        // Remove only our own stack entries; the streaming director can still hold its locks.
        Controller->SetIgnoreMoveInput(false);
        Controller->SetIgnoreLookInput(false);
        LockedController.Reset();
    }
    bOpen = Open;
}

void AReaBullyAreaNavigation::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    SetOpen(false);
    Super::EndPlay(EndPlayReason);
}

bool AReaBullyAreaNavigation::HandleInput(const FInputKeyEventArgs& Event)
{
    if (Event.Key == EKeys::Tab)
    {
        if (Event.Event == IE_Pressed) SetOpen(!bOpen);
        return true;
    }
    if (!bOpen) return false;
    if (Event.Event == IE_Pressed || Event.Event == IE_Repeat)
    {
        if (Event.Key == EKeys::Escape) SetOpen(false);
        else if (Event.Key == EKeys::Up) Selected = (Selected + Data->Entries.Num() - 1) % Data->Entries.Num();
        else if (Event.Key == EKeys::Down) Selected = (Selected + 1) % Data->Entries.Num();
        else if (Event.Key == EKeys::PageUp) Selected = FMath::Max(0, Selected - 10);
        else if (Event.Key == EKeys::PageDown) Selected = FMath::Min(Data->Entries.Num() - 1, Selected + 10);
        else if (Event.Key == EKeys::Enter && Event.Event == IE_Pressed) Travel();
    }
    // Do not feed gameplay keys (doors, garage, jump, quit) to PlayerInput while the menu owns focus.
    return true;
}

void AReaBullyAreaNavigation::Travel()
{
    APlayerController* Controller = GetWorld()->GetFirstPlayerController();
    auto* Character = Controller ? Cast<ACharacter>(Controller->GetPawn()) : nullptr;
    if (!bVerified || !Character || !Data->Entries.IsValidIndex(Selected)) return;
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It)
    {
        if (!It->IsReady()) return;
        const auto& Entry = Data->Entries[Selected];
        const FVector Destination = Entry.PositionFeet + FVector(0, 0, Character->GetCapsuleComponent()->GetScaledCapsuleHalfHeight() + 2);
        if (It->RequestTravel(Destination, Entry.Area))
        {
            Controller->SetControlRotation(FRotator(-8, Entry.YawDegrees, 0));
            Character->GetCharacterMovement()->StopMovementImmediately();
            UE_LOG(LogTemp, Display, TEXT("REA_AREA_NAVIGATION TRAVEL %s %s"), *Entry.Area.ToString(), *Entry.SourceId);
            SetOpen(false);
        }
        return;
    }
}

FString AReaBullyAreaNavigation::AreaLabel(FName Area) const
{
    if (bVerified) for (const auto& Entry : Data->Entries)
        if (Entry.Area == Area) return Entry.DisplayName;
    return FString();
}

void AReaBullyAreaNavigation::DrawMenu(AHUD* HUD, UCanvas* Canvas)
{
    if (!bOpen || !HUD || !Canvas || !GEngine) return;
    const float Scale = FMath::Clamp(Canvas->SizeX / 1600.0f, .65f, 1.5f);
    const float Width = 650 * Scale, Height = 465 * Scale;
    const float X = (Canvas->SizeX - Width) / 2, Y = (Canvas->SizeY - Height) / 2;
    HUD->DrawRect(FLinearColor(.015f, .025f, .035f, .96f), X, Y, Width, Height);
    HUD->DrawText(TEXT("ESPLORA BULLWORTH"), FLinearColor::White, X + 24 * Scale, Y + 20 * Scale, GEngine->GetMediumFont(), Scale);
    HUD->DrawText(TEXT("Scegli un'area"), FLinearColor(.7f, .8f, .84f), X + 24 * Scale, Y + 53 * Scale, GEngine->GetSmallFont(), Scale);
    const int32 First = FMath::Clamp(Selected - 5, 0, FMath::Max(0, Data->Entries.Num() - 10));
    for (int32 Index = First; Index < FMath::Min(First + 10, Data->Entries.Num()); ++Index)
    {
        const float RowY = Y + (88 + (Index - First) * 29) * Scale;
        if (Index == Selected) HUD->DrawRect(FLinearColor(.12f, .27f, .30f, 1), X + 14 * Scale, RowY - 3 * Scale, Width - 28 * Scale, 28 * Scale);
        HUD->DrawText(Data->Entries[Index].DisplayName, FLinearColor::White, X + 27 * Scale, RowY, GEngine->GetSmallFont(), Scale);
    }
    HUD->DrawText(FString::Printf(TEXT("%d / %d    Frecce  seleziona    Invio  viaggia"), Selected + 1, Data->Entries.Num()),
        FLinearColor(.7f, .8f, .84f), X + 24 * Scale, Y + 393 * Scale, GEngine->GetSmallFont(), Scale);
    HUD->DrawText(TEXT("PagSu / PagGiu  scorri    Tab / Esc  chiudi"), FLinearColor(.7f, .8f, .84f),
        X + 24 * Scale, Y + 423 * Scale, GEngine->GetSmallFont(), Scale);
}
