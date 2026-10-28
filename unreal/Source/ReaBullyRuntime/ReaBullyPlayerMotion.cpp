#include "ReaBullyExploration.h"
#include "ReaBullyAnimInstance.h"
#include "Animation/AnimSequence.h"
#include "Components/SkeletalMeshComponent.h"
#include "GameFramework/CharacterMovementComponent.h"

void AReaBullyExplorer::RequestJump()
{
    if (!Actions) { Jump(); return; }
    if (ActionPhase != EReaBullyActionPhase::Locomotion || !GetCharacterMovement()->IsMovingOnGround()) return;
    ActiveJump = Actions->Standing;
    if (GetVelocity().Size2D() > 80.0f)
    {
        UAnimSingleNodeInstance* Animation = GetMesh()->GetSingleNodeInstance();
        const float Length = Animation ? Animation->GetLength() : 0.0f;
        const float Stride = Length > SMALL_NUMBER ? FMath::Fmod(Animation->GetCurrentTime() / Length, 1.0f) : 0.0f;
        // Reconstructed foot selection; original ACT condition execution is not implemented.
        ActiveJump = Stride < 0.5f ? Actions->JogLeft : Actions->JogRight;
        if (bSprintRequested)
        {
            ActiveJump.GroundPlayRate = Actions->SprintGroundPlayRate;
            ActiveJump.LandingEnd = Actions->SprintLandingEnd;
        }
    }
    if (!ActiveJump.Clip) return;
    ActionPhase = EReaBullyActionPhase::Windup;
    PhaseSeconds = 0.0f;
    bJumpLaunchPending = false;
}

void AReaBullyExplorer::OnJumped_Implementation()
{
    Super::OnJumped_Implementation();
    if (!Actions || ActionPhase != EReaBullyActionPhase::Windup) return;
    ActionPhase = EReaBullyActionPhase::Airborne;
    PhaseSeconds = 0.0f;
    bJumpLaunchPending = false;
    // Keep the verified capsule physics. ARROW height alone is insufficient for JOGJUMP poses.
    EstimatedFlightSeconds = FMath::Max(0.1f, 2.0f * GetCharacterMovement()->JumpZVelocity
        / FMath::Abs(GetCharacterMovement()->GetGravityZ()));
}

void AReaBullyExplorer::OnMovementModeChanged(EMovementMode PreviousMovementMode, uint8 PreviousCustomMode)
{
    Super::OnMovementModeChanged(PreviousMovementMode, PreviousCustomMode);
    if (Actions && GetCharacterMovement()->IsFalling()
        && ActionPhase != EReaBullyActionPhase::Airborne && !bJumpLaunchPending)
    {
        ActionPhase = EReaBullyActionPhase::Falling;
        PhaseSeconds = 0.0f;
    }
}

void AReaBullyExplorer::Landed(const FHitResult& Hit)
{
    Super::Landed(Hit);
    if (!Actions) return;
    bLandingFromJump = ActionPhase == EReaBullyActionPhase::Airborne;
    ActionPhase = EReaBullyActionPhase::Landing;
    PhaseSeconds = 0.0f;
    bJumpLaunchPending = false;
    if (bLandingFromJump) ++CompletedJumpCount;
}

void AReaBullyExplorer::SetActionPose(UAnimSequence* Clip, float SampleTime, float DeltaSeconds)
{
    UReaBullyAnimInstance* Animation = Cast<UReaBullyAnimInstance>(GetMesh()->GetAnimInstance());
    if (!Animation) return;
    if (Clip && Animation->ActionClip != Clip)
    {
        Animation->PreviousActionClip = Animation->ActionWeight > SMALL_NUMBER ? Animation->ActionClip : nullptr;
        Animation->PreviousActionTime = Animation->ActionTime;
        Animation->ClipBlend = Animation->PreviousActionClip ? 0.0f : 1.0f;
        Animation->ActionClip = Clip;
    }
    if (Clip) Animation->ActionTime = FMath::Clamp(SampleTime, 0.0f, Clip->GetPlayLength());
    const float BlendRate = 1.0f / FMath::Max(Actions->BlendSeconds, 0.001f);
    Animation->ActionWeight = FMath::FInterpConstantTo(Animation->ActionWeight, Clip ? 1.0f : 0.0f, DeltaSeconds, BlendRate);
    Animation->ClipBlend = FMath::FInterpConstantTo(Animation->ClipBlend, 1.0f, DeltaSeconds, BlendRate);
    if (Animation->ClipBlend >= 1.0f) Animation->PreviousActionClip = nullptr;
    if (Animation->ActionWeight <= 0.0f) Animation->ActionClip = nullptr;
}

void AReaBullyExplorer::UpdateAction(float DeltaSeconds)
{
    if (!Actions) return;
    PhaseSeconds += DeltaSeconds;
    switch (ActionPhase)
    {
    case EReaBullyActionPhase::Windup:
        SetActionPose(ActiveJump.Clip, FMath::Min(PhaseSeconds * ActiveJump.GroundPlayRate, ActiveJump.WindupEnd), DeltaSeconds);
        if (!bJumpLaunchPending && PhaseSeconds * ActiveJump.GroundPlayRate >= ActiveJump.WindupEnd)
        {
            bJumpLaunchPending = true;
            Jump();
        }
        // Cancel cleanly if an overhead obstacle prevents launch.
        if (bJumpLaunchPending && PhaseSeconds > ActiveJump.WindupEnd / ActiveJump.GroundPlayRate + 0.2f)
        {
            bJumpLaunchPending = false;
            StopJumping();
            ActionPhase = EReaBullyActionPhase::Locomotion;
        }
        break;
    case EReaBullyActionPhase::Airborne:
        StopJumping();
        SetActionPose(ActiveJump.Clip, FMath::Lerp(ActiveJump.WindupEnd, ActiveJump.FlightEnd,
            FMath::Clamp(PhaseSeconds / EstimatedFlightSeconds, 0.0f, 1.0f)), DeltaSeconds);
        if (PhaseSeconds > EstimatedFlightSeconds + 0.2f)
        {
            ActionPhase = EReaBullyActionPhase::Falling;
            PhaseSeconds = 0.0f;
        }
        break;
    case EReaBullyActionPhase::Landing:
    {
        UAnimSequence* Clip = bLandingFromJump ? ActiveJump.Clip.Get() : Actions->FallLand.Get();
        const float Start = bLandingFromJump ? ActiveJump.LandingStart : 0.0f;
        const float End = bLandingFromJump ? ActiveJump.LandingEnd : (Clip ? Clip->GetPlayLength() : 0.0f);
        const float Rate = bLandingFromJump ? ActiveJump.GroundPlayRate : 1.0f;
        const float Time = Start + PhaseSeconds * Rate;
        SetActionPose(Clip, FMath::Min(Time, End), DeltaSeconds);
        if (Time >= End)
        {
            ActionPhase = EReaBullyActionPhase::Locomotion;
            PhaseSeconds = 0.0f;
        }
        break;
    }
    case EReaBullyActionPhase::Falling:
    {
        const float FirstLength = Actions->Fall ? Actions->Fall->GetPlayLength() : 0.0f;
        UAnimSequence* Clip = PhaseSeconds < FirstLength ? Actions->Fall.Get() : Actions->FallFlail.Get();
        // Original loop flags are not established: hold the final pose instead of inventing a loop.
        SetActionPose(Clip, PhaseSeconds < FirstLength ? PhaseSeconds : PhaseSeconds - FirstLength, DeltaSeconds);
        break;
    }
    default:
        SetActionPose(nullptr, 0.0f, DeltaSeconds);
        break;
    }
}
