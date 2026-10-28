#include "ReaBullyAnimInstance.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimSingleNodeInstanceProxy.h"
#include "Animation/AnimationPoseData.h"
#include "AnimationRuntime.h"

namespace
{
class FReaBullyAnimProxy : public FAnimSingleNodeInstanceProxy
{
public:
    explicit FReaBullyAnimProxy(UAnimInstance* Instance) : FAnimSingleNodeInstanceProxy(Instance) {}
    virtual void PreUpdate(UAnimInstance* Instance, float DeltaSeconds) override
    {
        FAnimSingleNodeInstanceProxy::PreUpdate(Instance, DeltaSeconds);
        const UReaBullyAnimInstance* Recovered = CastChecked<UReaBullyAnimInstance>(Instance);
        Action = Recovered->ActionClip;
        Time = Recovered->ActionTime;
        Weight = FMath::Clamp(Recovered->ActionWeight, 0.0f, 1.0f);
        PreviousAction = Recovered->PreviousActionClip;
        PreviousTime = Recovered->PreviousActionTime;
        ClipWeight = Recovered->ClipBlend;
    }
    virtual bool Evaluate(FPoseContext& Output) override
    {
        FAnimSingleNodeInstanceProxy::Evaluate(Output);
        if (Action && Weight > ZERO_ANIMWEIGHT_THRESH)
        {
            FPoseContext ActionPose(Output);
            FAnimationPoseData ActionData(ActionPose);
            FAnimExtractContext Context(FMath::Clamp<double>(Time, 0.0, Action->GetPlayLength()), false);
            Action->GetAnimationPose(ActionData, Context);
            if (PreviousAction && ClipWeight < 1.0f)
            {
                FPoseContext PreviousPose(Output);
                FAnimationPoseData PreviousData(PreviousPose);
                FAnimExtractContext PreviousContext(FMath::Clamp<double>(PreviousTime, 0.0, PreviousAction->GetPlayLength()), false);
                PreviousAction->GetAnimationPose(PreviousData, PreviousContext);
                FAnimationRuntime::BlendTwoPosesTogetherInPlace(ActionData, PreviousData, ClipWeight);
            }
            FAnimationPoseData OutputData(Output);
            FAnimationRuntime::BlendTwoPosesTogetherInPlace(OutputData, ActionData, 1.0f - Weight);
        }
        return true;
    }
private:
    UAnimSequence* Action = nullptr;
    UAnimSequence* PreviousAction = nullptr;
    float Time = 0.0f;
    float PreviousTime = 0.0f;
    float Weight = 0.0f;
    float ClipWeight = 1.0f;
};
}

FAnimInstanceProxy* UReaBullyAnimInstance::CreateAnimInstanceProxy() { return new FReaBullyAnimProxy(this); }
void UReaBullyAnimInstance::DestroyAnimInstanceProxy(FAnimInstanceProxy* Proxy) { delete Proxy; }
