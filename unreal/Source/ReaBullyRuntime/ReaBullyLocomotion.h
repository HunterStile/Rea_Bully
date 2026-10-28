#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "ReaBullyLocomotion.generated.h"

class UAnimSequence;
class UBlendSpace1D;

/** Editor construction of a native blend space from independently recovered clips. */
UCLASS()
class REABULLYRUNTIME_API UReaBullyCharacterTools : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable, Category="Recovered assets")
    static UBlendSpace1D* CreateRecoveredLocomotion(const FString& PackagePath,
        const TArray<UAnimSequence*>& Clips, const TArray<float>& Speeds);
};
