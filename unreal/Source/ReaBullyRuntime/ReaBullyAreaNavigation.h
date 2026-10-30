#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "GameFramework/Actor.h"
#include "ReaBullyAreaNavigation.generated.h"

class AHUD;
class APlayerController;
struct FInputKeyEventArgs;

/** An original feet coordinate successfully exercised by the world tour. */
USTRUCT(BlueprintType)
struct FReaBullyAreaEntry
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere) FString SourceId;
    UPROPERTY(EditAnywhere) FName Area;
    UPROPERTY(EditAnywhere) FString DisplayName;
    UPROPERTY(EditAnywhere) FVector PositionFeet = FVector::ZeroVector;
    UPROPERTY(EditAnywhere) float YawDegrees = 0;
};

UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyAreaNavigationData : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) TArray<FReaBullyAreaEntry> Entries;
    UPROPERTY(EditAnywhere) FString RuntimeSHA256;
    UPROPERTY(EditAnywhere) FString EvidenceSHA256;
    UPROPERTY(EditAnywhere) TMap<FString, FString> SceneAssetHashes;
};

/** Reconstructed direct travel menu; available only for the verified saved scene. */
UCLASS(NotBlueprintable)
class REABULLYRUNTIME_API AReaBullyAreaNavigation : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyAreaNavigation();
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
    bool HandleInput(const FInputKeyEventArgs& Event);
    void DrawMenu(AHUD* HUD, class UCanvas* Canvas);
    bool IsAvailable() const { return bVerified; }
    bool IsOpen() const { return bOpen; }
    int32 EntryCount() const { return bVerified ? Data->Entries.Num() : 0; }
    const FReaBullyAreaEntry* SelectedEntry() const { return bVerified && Data->Entries.IsValidIndex(Selected) ? &Data->Entries[Selected] : nullptr; }
    FString AreaLabel(FName Area) const;
private:
    UPROPERTY() TObjectPtr<UReaBullyAreaNavigationData> Data;
    TWeakObjectPtr<APlayerController> LockedController;
    bool bVerified = false;
    bool bOpen = false;
    int32 Selected = 0;
    bool VerifySavedScene() const;
    void SetOpen(bool Open);
    void Travel();
};
