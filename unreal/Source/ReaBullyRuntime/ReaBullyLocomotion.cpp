#include "ReaBullyLocomotion.h"
#include "Animation/AnimSequence.h"
#include "Animation/BlendSpace1D.h"
#include "Misc/PackageName.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

UBlendSpace1D* UReaBullyCharacterTools::CreateRecoveredLocomotion(const FString& PackagePath,
    const TArray<UAnimSequence*>& Clips, const TArray<float>& Speeds)
{
#if WITH_EDITOR
    if (!PackagePath.StartsWith(TEXT("/Game/")) || !FPackageName::IsValidLongPackageName(PackagePath)
        || Clips.Num() != 4 || Speeds.Num() != Clips.Num()) return nullptr;
    for (int32 Index = 0; Index < Clips.Num(); ++Index)
        if (!Clips[Index] || !Clips[Index]->GetSkeleton() || Clips[Index]->GetSkeleton() != Clips[0]->GetSkeleton()
            || !FMath::IsFinite(Speeds[Index]) || (Index > 0 && Speeds[Index] <= Speeds[Index - 1])) return nullptr;
    UPackage* Package = CreatePackage(*PackagePath);
    const FName Name(*FPackageName::GetShortName(PackagePath));
    UBlendSpace1D* Blend = FindObject<UBlendSpace1D>(Package, *Name.ToString());
    if (!Blend) Blend = NewObject<UBlendSpace1D>(Package, Name, RF_Public | RF_Standalone);
    Blend->Modify();
    Blend->SetSkeleton(Clips[0]->GetSkeleton());
    // Use the same reflected editor properties that the native asset editor edits.
    FStructProperty* Parameters = FindFProperty<FStructProperty>(UBlendSpace::StaticClass(), TEXT("BlendParameters"));
    FFloatProperty* Smoothing = FindFProperty<FFloatProperty>(UBlendSpace::StaticClass(), TEXT("TargetWeightInterpolationSpeedPerSec"));
    if (!Parameters || !Smoothing) return nullptr;
    FBlendParameter* SpeedAxis = Parameters->ContainerPtrToValuePtr<FBlendParameter>(Blend, 0);
    SpeedAxis->DisplayName = TEXT("Speed (cm/s)");
    SpeedAxis->Min = 0.0f;
    SpeedAxis->Max = Speeds.Last();
    SpeedAxis->GridNum = 16;
    SpeedAxis->bSnapToGrid = false;
    Smoothing->SetPropertyValue_InContainer(Blend, 6.0f);
    while (Blend->GetNumberOfBlendSamples() > 0) Blend->DeleteSample(0);
    for (int32 Index = 0; Index < Clips.Num(); ++Index)
        if (Blend->AddSample(Clips[Index], FVector(Speeds[Index], 0.0, 0.0)) == INDEX_NONE) return nullptr;
    Blend->ValidateSampleData();
    Blend->ResampleData();
    Blend->PostEditChange();
    Blend->MarkPackageDirty();
    return Blend;
#else
    return nullptr;
#endif
}
