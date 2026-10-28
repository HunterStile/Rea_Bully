#include "ReaBullySmokeProbe.h"
#include "ReaBullyExploration.h"
#include "ReaBullyAnimInstance.h"
#include "Animation/AnimSequence.h"
#include "Components/BoxComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Animation/BlendSpace1D.h"
#include "Engine/SkeletalMesh.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/PlayerInput.h"
#include "GameFramework/WorldSettings.h"
#include "InputKeyEventArgs.h"
#include "HAL/PlatformMisc.h"
#include "Misc/FileHelper.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "UnrealClient.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace
{
double FacingAlignment(AReaBullyExplorer* Player)
{
    USkeletalMeshComponent* Mesh = Player->GetMesh();
    if (!Mesh->GetSkeletalMeshAsset()) return -1.0;
    const FReferenceSkeleton& Reference = Mesh->GetSkeletalMeshAsset()->GetRefSkeleton();
    int32 Head = INDEX_NONE;
    for (int32 Index = 0; Index < Reference.GetNum(); ++Index)
        if (Reference.GetBoneName(Index).ToString().Contains(TEXT("Head"))) { Head = Index; break; }
    const TArray<FTransform>& Pose = Mesh->GetComponentSpaceTransforms();
    if (Head == INDEX_NONE || !Pose.IsValidIndex(Head)) return -1.0;
    FTransform Rest = FTransform::Identity;
    for (int32 Index = Head; Index != INDEX_NONE; Index = Reference.GetParentIndex(Index))
        Rest = Rest * Reference.GetRefBonePose()[Index];
    // The visible face points NIF -Y / UE +Y in the independently verified bind pose.
    const FQuat Delta = Pose[Head].GetRotation() * Rest.GetRotation().Inverse();
    FVector Forward = Mesh->GetComponentQuat().RotateVector(Delta.RotateVector(FVector(0.0, 1.0, 0.0)));
    Forward.Z = 0.0;
    return FVector::DotProduct(Forward.GetSafeNormal(), Player->GetActorForwardVector());
}
}

AReaBullySmokeProbe::AReaBullySmokeProbe()
{
    PrimaryActorTick.bCanEverTick = true;
    Results = MakeShared<FJsonObject>();
}

void AReaBullySmokeProbe::Key(const FKey& Input, EInputEvent Event)
{
    if (Controller.IsValid() && Controller->PlayerInput)
        Controller->InputKey(FInputKeyEventArgs(nullptr, FInputDeviceId::CreateFromInternalId(0),
            Input, Event, Event == IE_Released ? 0.0f : 1.0f, false, FPlatformTime::Cycles64()));
}

void AReaBullySmokeProbe::NextPhase() { ++Phase; PhaseTime = 0.0f; }

void AReaBullySmokeProbe::Capture(const FString& Suffix)
{
    FString Path;
    if (FParse::Value(FCommandLine::Get(), TEXT("ReaBullyScreenshot="), Path))
    {
        Path = FPaths::GetBaseFilename(Path, false) + TEXT("-") + Suffix + TEXT(".png");
        FScreenshotRequest::RequestScreenshot(Path, true, false);
        Results->SetStringField(Suffix + TEXT("_screenshot"), Path);
    }
}

void AReaBullySmokeProbe::Finish(const FString& Error)
{
    SetActorTickEnabled(false);
    Key(EKeys::W, IE_Released);
    Key(EKeys::SpaceBar, IE_Released);
    Key(EKeys::LeftShift, IE_Released);
    bool Passed = Error.IsEmpty();
    for (const FString& Check : { TEXT("grounded_on_original_geometry"), TEXT("keyboard_moves_player"),
          TEXT("jump_rises_and_lands"), TEXT("sprint_increases_speed"), TEXT("wall_blocks_capsule"),
          TEXT("reset_respawns_player"), TEXT("fall_respawns_player"), TEXT("original_skeletal_character"),
          TEXT("locomotion_changes_original_bones"), TEXT("first_person_toggle"), TEXT("third_person_toggle"),
          TEXT("character_faces_movement"), TEXT("standing_jump_uses_original_action"),
          TEXT("jump_phases_follow_grounding"), TEXT("running_jump_uses_original_action"),
          TEXT("ledge_fall_and_landing_actions"), TEXT("jump_returns_to_locomotion") })
        Passed &= Results->HasField(Check) && Results->GetBoolField(Check);
    Results->SetBoolField(TEXT("passed"), Passed);
    Results->SetStringField(TEXT("error"), Error);
    Results->SetStringField(TEXT("map"), GetWorld()->GetMapName());
    Results->SetStringField(TEXT("method"), TEXT("Real game world, injected keyboard input, CharacterMovement physics and scene traces. Temporary test wall only during smoke test."));
    Results->SetNumberField(TEXT("simulated_seconds"), TotalTime);
    FString Json;
    auto Writer = TJsonWriterFactory<>::Create(&Json);
    FJsonSerializer::Serialize(Results.ToSharedRef(), Writer);
    if (!FFileHelper::SaveStringToFile(Json, *ReportPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
        Passed = false;
    UE_LOG(LogTemp, Display, TEXT("REA_BULLY_SMOKE %s %s"), Passed ? TEXT("PASS") : TEXT("FAIL"), *ReportPath);
    FPlatformMisc::RequestExitWithStatus(false, Passed ? 0 : 1);
}

void AReaBullySmokeProbe::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    TotalTime += DeltaSeconds;
    PhaseTime += DeltaSeconds;
    if (TotalTime > 25.0f) { Finish(TEXT("Timed out while exercising exploration")); return; }
    if (Phase == 0)
    {
        Controller = Cast<AReaBullyPlayerController>(GetWorld()->GetFirstPlayerController());
        if (Controller.IsValid()) Player = Cast<AReaBullyExplorer>(Controller->GetPawn());
        if (!Player.IsValid()) return;
        if (PhaseTime < 1.0f) return;
        const auto& Floor = Player->GetCharacterMovement()->CurrentFloor;
        Results->SetBoolField(TEXT("grounded_on_original_geometry"), Player->GetCharacterMovement()->IsMovingOnGround()
            && Floor.HitResult.GetActor() && Floor.HitResult.GetActor()->IsA<AStaticMeshActor>());
        Results->SetStringField(TEXT("floor_actor"), GetNameSafe(Floor.HitResult.GetActor()));
        int32 MeshCount = 0;
        for (TActorIterator<AStaticMeshActor> It(GetWorld()); It; ++It) ++MeshCount;
        Results->SetNumberField(TEXT("original_mesh_actors"), MeshCount);
        USkeletalMeshComponent* Mesh = Player->GetMesh();
        UAnimSingleNodeInstance* Animation = Mesh->GetSingleNodeInstance();
        Results->SetBoolField(TEXT("original_skeletal_character"), Mesh->GetSkeletalMeshAsset()
            && Mesh->GetSkeletalMeshAsset()->GetPathName() == TEXT("/Game/Original/JimmyActions/SK_Jimmy.SK_Jimmy")
            && Mesh->GetNumBones() == 36 && Animation && Cast<UBlendSpace1D>(Animation->GetCurrentAsset())
            && !Player->IsFirstPerson());
        Results->SetNumberField(TEXT("character_bones"), Mesh->GetNumBones());
        Results->SetStringField(TEXT("character_mesh"), GetPathNameSafe(Mesh->GetSkeletalMeshAsset()));
        IdlePose = Mesh->GetComponentSpaceTransforms();
        FString Screenshot;
        if (FParse::Value(FCommandLine::Get(), TEXT("ReaBullyScreenshot="), Screenshot))
        {
            FScreenshotRequest::RequestScreenshot(Screenshot, true, false);
            Results->SetStringField(TEXT("screenshot"), Screenshot);
        }
        StartPosition = Player->GetActorLocation();
        Key(EKeys::W, IE_Pressed);
        NextPhase();
    }
    else if (Phase < 6 && !Player.IsValid()) { Finish(TEXT("Player disappeared")); }
    else if (Phase == 1)
    {
        if (!bMovementScreenshot && PhaseTime >= 0.45f)
        {
            FString Screenshot;
            if (FParse::Value(FCommandLine::Get(), TEXT("ReaBullyScreenshot="), Screenshot))
            {
                Screenshot = FPaths::GetBaseFilename(Screenshot, false) + TEXT("-moving.png");
                FScreenshotRequest::RequestScreenshot(Screenshot, true, false);
                Results->SetStringField(TEXT("movement_screenshot"), Screenshot);
            }
            bMovementScreenshot = true;
        }
        if (PhaseTime < 1.0f) return;
        Key(EKeys::W, IE_Released);
        const float Distance = FVector::Dist2D(StartPosition, Player->GetActorLocation());
        Results->SetNumberField(TEXT("walk_distance_cm"), Distance);
        Results->SetBoolField(TEXT("keyboard_moves_player"), Distance > 100.0f && Distance < 550.0f);
        const TArray<FTransform>& MovingPose = Player->GetMesh()->GetComponentSpaceTransforms();
        int32 ChangedBones = 0;
        double MaximumAngle = 0.0;
        for (int32 Index = 0; Index < FMath::Min(IdlePose.Num(), MovingPose.Num()); ++Index)
        {
            const double Angle = FMath::RadiansToDegrees(IdlePose[Index].GetRotation().AngularDistance(MovingPose[Index].GetRotation()));
            MaximumAngle = FMath::Max(MaximumAngle, Angle);
            if (Angle > 2.0) ++ChangedBones;
        }
        Results->SetNumberField(TEXT("moving_bones_over_2_degrees"), ChangedBones);
        Results->SetNumberField(TEXT("maximum_pose_change_degrees"), MaximumAngle);
        Results->SetBoolField(TEXT("locomotion_changes_original_bones"), ChangedBones >= 4);
        const double Facing = FacingAlignment(Player.Get());
        Results->SetNumberField(TEXT("character_facing_dot_movement"), Facing);
        Results->SetBoolField(TEXT("character_faces_movement"), Facing > 0.85);
        Key(EKeys::V, IE_Pressed);
        NextPhase();
    }
    else if (Phase == 2 && PhaseTime >= 0.5f)
    {
        Results->SetBoolField(TEXT("first_person_toggle"), Player->IsFirstPerson());
        Key(EKeys::V, IE_Released);
        JumpBase = Player->GetActorLocation().Z;
        JumpMaximum = JumpBase;
        Key(EKeys::SpaceBar, IE_Pressed);
        NextPhase();
    }
    else if (Phase == 3)
    {
        UReaBullyAnimInstance* Animation = Cast<UReaBullyAnimInstance>(Player->GetMesh()->GetAnimInstance());
        bJumpWindup |= Player->GetActionPhase() == EReaBullyActionPhase::Windup
            && Player->GetCharacterMovement()->IsMovingOnGround();
        int32 ActionBones = 0;
        const TArray<FTransform>& ActionPose = Player->GetMesh()->GetComponentSpaceTransforms();
        for (int32 Index = 0; Index < FMath::Min(IdlePose.Num(), ActionPose.Num()); ++Index)
            if (FMath::RadiansToDegrees(IdlePose[Index].GetRotation().AngularDistance(ActionPose[Index].GetRotation())) > 20.0) ++ActionBones;
        const double PreviousBoneCount = Results->HasField(TEXT("standing_jump_pose_bones_over_20_degrees"))
            ? Results->GetNumberField(TEXT("standing_jump_pose_bones_over_20_degrees")) : 0.0;
        Results->SetNumberField(TEXT("standing_jump_pose_bones_over_20_degrees"), FMath::Max(PreviousBoneCount, static_cast<double>(ActionBones)));
        bJumpAirborne |= Player->GetActionPhase() == EReaBullyActionPhase::Airborne
            && Player->GetCharacterMovement()->IsFalling() && Animation && Animation->ActionClip
            && Animation->ActionClip->GetName().EndsWith(TEXT("_IDLEJUMP")) && Animation->ActionWeight > 0.9f && ActionBones >= 4;
        bJumpLanding |= Player->GetActionPhase() == EReaBullyActionPhase::Landing
            && Player->GetCharacterMovement()->IsMovingOnGround();
        if (!bStandingScreenshot && PhaseTime >= 0.5f)
        {
            Capture(TEXT("jump-standing"));
            bStandingScreenshot = true;
        }
        if (!bReturnedToThirdPerson)
        {
            Key(EKeys::V, IE_Pressed);
            bReturnedToThirdPerson = true;
        }
        JumpMaximum = FMath::Max(JumpMaximum, static_cast<float>(Player->GetActorLocation().Z));
        if (PhaseTime >= 0.15f) Key(EKeys::SpaceBar, IE_Released);
        if (PhaseTime >= 1.4f)
        {
            Results->SetBoolField(TEXT("standing_jump_uses_original_action"), bJumpAirborne);
            Results->SetBoolField(TEXT("jump_phases_follow_grounding"), bJumpWindup && bJumpAirborne && bJumpLanding);
            Results->SetBoolField(TEXT("jump_returns_to_locomotion"), Player->GetActionPhase() == EReaBullyActionPhase::Locomotion
                && Player->GetCompletedJumpCount() == 1 && Animation && Animation->ActionWeight < 0.1f);
            Key(EKeys::V, IE_Released);
            Results->SetBoolField(TEXT("third_person_toggle"), !Player->IsFirstPerson());
            Results->SetNumberField(TEXT("jump_height_cm"), JumpMaximum - JumpBase);
            Results->SetBoolField(TEXT("jump_rises_and_lands"), JumpMaximum - JumpBase > 45.0f && Player->GetCharacterMovement()->IsMovingOnGround());
            Key(EKeys::LeftShift, IE_Pressed);
            StartPosition = Player->GetActorLocation();
            Key(EKeys::W, IE_Pressed);
            NextPhase();
        }
    }
    else if (Phase == 4)
    {
        if (!bRunningJumpRequested && PhaseTime >= 0.15f)
        {
            Key(EKeys::SpaceBar, IE_Pressed);
            bRunningJumpRequested = true;
        }
        if (PhaseTime >= 0.3f) Key(EKeys::SpaceBar, IE_Released);
        UReaBullyAnimInstance* Animation = Cast<UReaBullyAnimInstance>(Player->GetMesh()->GetAnimInstance());
        bRunningJumpAnimated |= Animation && Animation->ActionClip
            && Animation->ActionClip->GetName().Contains(TEXT("_JOGJUMP_")) && Animation->ActionWeight > 0.9f
            && Player->GetCharacterMovement()->IsFalling();
        if (!bRunningScreenshot && PhaseTime >= 0.6f)
        {
            Capture(TEXT("jump-running"));
            bRunningScreenshot = true;
        }
        SprintMaximum = FMath::Max(SprintMaximum, static_cast<float>(Player->GetVelocity().Size2D()));
        if (PhaseTime < 0.7f) return;
        const float SprintDistance = FVector::Dist2D(StartPosition, Player->GetActorLocation());
        Results->SetNumberField(TEXT("sprint_peak_speed_cm_s"), SprintMaximum);
        Results->SetNumberField(TEXT("sprint_distance_cm"), SprintDistance);
        Results->SetBoolField(TEXT("sprint_increases_speed"), SprintMaximum > 500.0f && SprintDistance > 200.0f);
        Key(EKeys::W, IE_Released);
        Key(EKeys::LeftShift, IE_Released);
        const FVector Forward = FRotator(0.0, Controller->GetControlRotation().Yaw, 0.0).Vector();
        StartPosition = Player->GetActorLocation();
        WallPosition = StartPosition + Forward * 220.0f;
        Wall = GetWorld()->SpawnActor<AActor>();
        UBoxComponent* Box = NewObject<UBoxComponent>(Wall.Get());
        Wall->SetRootComponent(Box);
        Box->SetBoxExtent(FVector(15.0, 180.0, 160.0));
        Box->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        Box->SetCollisionObjectType(ECC_WorldStatic);
        Box->SetCollisionResponseToAllChannels(ECR_Block);
        Box->RegisterComponent();
        Wall->SetActorLocation(WallPosition);
        Wall->SetActorRotation(FRotator(0.0, Controller->GetControlRotation().Yaw, 0.0));
        Key(EKeys::W, IE_Pressed);
        NextPhase();
    }
    else if (Phase == 5 && PhaseTime >= 1.1f)
    {
        Results->SetBoolField(TEXT("running_jump_uses_original_action"), bRunningJumpAnimated
            && Player->GetCompletedJumpCount() == 2 && Player->GetCharacterMovement()->IsMovingOnGround());
        Key(EKeys::W, IE_Released);
        const FVector Forward = FRotator(0.0, Controller->GetControlRotation().Yaw, 0.0).Vector();
        const float Progress = FVector::DotProduct(Player->GetActorLocation() - StartPosition, Forward);
        Results->SetNumberField(TEXT("wall_test_progress_cm"), Progress);
        Results->SetBoolField(TEXT("wall_blocks_capsule"), Progress > 80.0f && Progress < 190.0f);
        if (Wall.IsValid()) Wall->Destroy();
        StartPosition = Player->GetActorLocation();
        Key(EKeys::R, IE_Pressed);
        NextPhase();
    }
    else if (Phase == 6 && PhaseTime >= 1.0f)
    {
        Key(EKeys::R, IE_Released);
        AReaBullyExplorer* Replacement = Cast<AReaBullyExplorer>(Controller->GetPawn());
        Results->SetBoolField(TEXT("reset_respawns_player"), Replacement && Replacement != Player.Get()
            && Replacement->GetCharacterMovement()->IsMovingOnGround());
        if (!Results->GetBoolField(TEXT("reset_respawns_player")))
        {
            Finish(TEXT("Reset key did not produce a grounded replacement player"));
            return;
        }
        Player = Replacement;
        FVector AboveGround = Replacement->GetActorLocation();
        AboveGround.Z += 1800.0f;
        Replacement->SetActorLocation(AboveGround, false, nullptr, ETeleportType::TeleportPhysics);
        Replacement->GetCharacterMovement()->SetMovementMode(MOVE_Falling);
        NextPhase();
    }
    else if (Phase == 7)
    {
        if (!Player.IsValid()) { Finish(TEXT("Player disappeared during controlled ledge drop")); return; }
        UReaBullyAnimInstance* Animation = Cast<UReaBullyAnimInstance>(Player->GetMesh()->GetAnimInstance());
        const FString Clip = Animation && Animation->ActionClip ? Animation->ActionClip->GetName() : FString();
        bObservedFall |= Clip.EndsWith(TEXT("_FALL")) && Player->GetCharacterMovement()->IsFalling();
        bObservedFlail |= Clip.EndsWith(TEXT("_FALL_FLAIL")) && Player->GetCharacterMovement()->IsFalling();
        bObservedFallLand |= Clip.EndsWith(TEXT("_FALL_LAND")) && Player->GetCharacterMovement()->IsMovingOnGround();
        if (!bFallScreenshot && PhaseTime >= 1.35f) { Capture(TEXT("fall")); bFallScreenshot = true; }
        if (!bLandingScreenshot && bObservedFallLand && Animation->ActionTime > 0.1f)
        {
            Capture(TEXT("landing"));
            bLandingScreenshot = true;
        }
        if (PhaseTime < 3.0f) return;
        Results->SetBoolField(TEXT("ledge_fall_and_landing_actions"), bObservedFall && bObservedFlail && bObservedFallLand
            && Player->GetCharacterMovement()->IsMovingOnGround() && Player->GetActionPhase() == EReaBullyActionPhase::Locomotion);
        FVector BelowWorld = Player->GetActorLocation();
        BelowWorld.Z = GetWorld()->GetWorldSettings()->KillZ - 200.0f;
        Player->SetActorLocation(BelowWorld, false, nullptr, ETeleportType::TeleportPhysics);
        NextPhase();
    }
    else if (Phase == 8 && PhaseTime >= 1.2f)
    {
        AReaBullyExplorer* Replacement = Cast<AReaBullyExplorer>(Controller->GetPawn());
        Results->SetBoolField(TEXT("fall_respawns_player"), Replacement && Replacement != Player.Get()
            && Replacement->GetCharacterMovement()->IsMovingOnGround());
        Finish();
    }
}
