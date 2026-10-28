#pragma once

#include "CoreMinimal.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "ReaBullyAnimInstance.generated.h"

class UAnimSequence;

/** Keeps locomotion running underneath a time-addressed original action pose. */
UCLASS(Transient)
class REABULLYRUNTIME_API UReaBullyAnimInstance : public UAnimSingleNodeInstance
{
    GENERATED_BODY()
public:
    UPROPERTY(Transient) TObjectPtr<UAnimSequence> ActionClip;
    UPROPERTY(Transient) TObjectPtr<UAnimSequence> PreviousActionClip;
    float ActionTime = 0.0f;
    float ActionWeight = 0.0f;
    float PreviousActionTime = 0.0f;
    float ClipBlend = 1.0f;
protected:
    virtual FAnimInstanceProxy* CreateAnimInstanceProxy() override;
    virtual void DestroyAnimInstanceProxy(FAnimInstanceProxy* InProxy) override;
};
