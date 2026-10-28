#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "ReaBullyPlayerActions.generated.h"

class UAnimSequence;
class UBlendSpace1D;
class USkeletalMesh;

/** Source clip segments. Capsule physics remain independently controlled by Unreal. */
USTRUCT(BlueprintType)
struct FReaBullyJumpProfile
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> Clip;
    UPROPERTY(EditAnywhere) float WindupEnd = 0.0f;
    UPROPERTY(EditAnywhere) float FlightEnd = 0.0f;
    UPROPERTY(EditAnywhere) float LandingStart = 0.0f;
    UPROPERTY(EditAnywhere) float LandingEnd = 0.0f;
    UPROPERTY(EditAnywhere) float GroundPlayRate = 1.0f;
};

UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyPlayerActions : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) TObjectPtr<USkeletalMesh> Mesh;
    UPROPERTY(EditAnywhere) TObjectPtr<UBlendSpace1D> Locomotion;
    UPROPERTY(EditAnywhere) FReaBullyJumpProfile Standing;
    UPROPERTY(EditAnywhere) FReaBullyJumpProfile JogLeft;
    UPROPERTY(EditAnywhere) FReaBullyJumpProfile JogRight;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> Fall;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> FallFlail;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> FallLand;
    UPROPERTY(EditAnywhere) float BlendSeconds = 0.066667f;
    UPROPERTY(EditAnywhere) float SprintGroundPlayRate = 1.25f;
    UPROPERTY(EditAnywhere) float SprintLandingEnd = 0.7f;
};
