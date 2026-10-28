#include "ReaBullyWorldProbe.h"
#include "ReaBullyWorld.h"
#include "ReaBullyAmbient.h"
#include "ReaBullyWorldAudio.h"
#include "ReaBullyWorldDoor.h"
#include "ReaBullyWorldProp.h"
#include "ReaBullyExploration.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/BoxComponent.h"
#include "Components/CapsuleComponent.h"
#include "CollisionShape.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/PlayerInput.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMesh.h"
#include "Dom/JsonObject.h"
#include "HAL/PlatformTime.h"
#include "HAL/PlatformMisc.h"
#include "InputKeyEventArgs.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UnrealClient.h"

AReaBullyWorldProbe::AReaBullyWorldProbe()
{
    PrimaryActorTick.bCanEverTick = true;
    Started = FPlatformTime::Seconds();
    Results = MakeShared<FJsonObject>();
}

void AReaBullyWorldProbe::Key(bool Pressed)
{
    if (APlayerController* Controller = GetWorld()->GetFirstPlayerController())
        Controller->InputKey(FInputKeyEventArgs(nullptr, FInputDeviceId::CreateFromInternalId(0), EKeys::W,
            Pressed ? IE_Pressed : IE_Released, Pressed ? 1.0f : 0.0f, false, FPlatformTime::Cycles64()));
}

void AReaBullyWorldProbe::DoorKey(bool Pressed)
{
    bDoorKeyHeld = Pressed;
    if (APlayerController* Controller = GetWorld()->GetFirstPlayerController())
        Controller->InputKey(FInputKeyEventArgs(nullptr, FInputDeviceId::CreateFromInternalId(0), EKeys::E,
            Pressed ? IE_Pressed : IE_Released, Pressed ? 1.0f : 0.0f, false, FPlatformTime::Cycles64()));
}

void AReaBullyWorldProbe::RecordPlayerState(const FString& Label, AReaBullyExplorer* Player)
{
    auto State = MakeShared<FJsonObject>();
    State->SetStringField(TEXT("position"), Player->GetActorLocation().ToString());
    State->SetStringField(TEXT("velocity"), Player->GetVelocity().ToString());
    State->SetNumberField(TEXT("movement_mode"), Player->GetCharacterMovement()->MovementMode);
    State->SetBoolField(TEXT("grounded"), Player->GetCharacterMovement()->IsMovingOnGround());
    State->SetStringField(TEXT("floor_component"), GetPathNameSafe(Player->GetCharacterMovement()->CurrentFloor.HitResult.GetComponent()));
    State->SetNumberField(TEXT("capsule_radius"), Player->GetCapsuleComponent()->GetScaledCapsuleRadius());
    State->SetNumberField(TEXT("capsule_half_height"), Player->GetCapsuleComponent()->GetScaledCapsuleHalfHeight());
    for (bool Complex : {false, true})
    {
        FCollisionQueryParams Params(SCENE_QUERY_STAT(IntegratedFloorDiagnostic), Complex, Player);
        for (bool Capsule : {false, true})
        {
            FHitResult Hit;
            const FVector Start = Player->GetActorLocation() + FVector(0, 0, 20);
            const FVector End = Player->GetActorLocation() - FVector(0, 0, 150);
            const bool Found = Capsule ? GetWorld()->SweepSingleByChannel(Hit, Start, End, FQuat::Identity,
                ECC_Pawn, FCollisionShape::MakeCapsule(Player->GetCapsuleComponent()->GetScaledCapsuleRadius(),
                Player->GetCapsuleComponent()->GetScaledCapsuleHalfHeight()), Params) :
                GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params);
            auto Query = MakeShared<FJsonObject>();
            Query->SetBoolField(TEXT("hit"), Found);
            Query->SetBoolField(TEXT("start_penetrating"), Hit.bStartPenetrating);
            Query->SetStringField(TEXT("impact_normal"), Hit.ImpactNormal.ToString());
            Query->SetStringField(TEXT("normal"), Hit.Normal.ToString());
            Query->SetStringField(TEXT("impact_point"), Hit.ImpactPoint.ToString());
            Query->SetStringField(TEXT("component"), GetPathNameSafe(Hit.GetComponent()));
            State->SetObjectField(FString(Capsule ? TEXT("capsule_") : TEXT("ray_")) +
                (Complex ? TEXT("complex") : TEXT("simple")), Query);
        }
    }
    for (TActorIterator<AReaBullyZoneGeometry> It(GetWorld()); It; ++It)
    {
        TArray<UHierarchicalInstancedStaticMeshComponent*> Components;
        It->GetComponents(Components);
        for (auto* Component : Components)
            if (Component->ComponentHasTag(TEXT("OriginalCOL3")) && Component->GetStaticMesh() &&
                Component->GetStaticMesh()->GetPathName().Contains(TEXT("05140_fgrd_sc1b11")))
                State->SetStringField(TEXT("source_ground_mesh"), UReaBullyWorldPropTools::InspectCollisionMesh(Component->GetStaticMesh()));
    }
    Results->SetObjectField(Label, State);
}

void AReaBullyWorldProbe::Finish(const FString& Error)
{
    SetActorTickEnabled(false);
    Key(false);
    DoorKey(false);
    bool Passed = Error.IsEmpty();
    for (const FString& Check : {TEXT("world_streaming_ready"), TEXT("grounded_on_original_instances"),
        TEXT("original_player_skin"), TEXT("keyboard_moves_player"), TEXT("ambient_own_skins"),
        TEXT("original_patrol_moves"), TEXT("reset_waits_for_original_floor")})
        Passed &= Results->HasField(Check) && Results->GetBoolField(Check);
    if (FParse::Param(FCommandLine::Get(), TEXT("ReaBullyIntegrationProbe")))
    {
        for (const FString& Check : {TEXT("expanded_original_cast"), TEXT("ambient_on_original_floor"), TEXT("original_collision_instances"),
            TEXT("original_door_open_close"), TEXT("door_keyboard_input"), TEXT("door_travel_arrives_in_area"),
            TEXT("return_from_interior"), TEXT("dorm_door_keyboard_input"), TEXT("dorm_travel_arrives_in_area"),
            TEXT("return_from_dorm"), TEXT("recovered_audio_assets")})
            Passed &= Results->HasField(Check) && Results->GetBoolField(Check);
        if (FParse::Param(FCommandLine::Get(), TEXT("ReaBullyRequireAudio")))
            for (const FString& Check : {TEXT("footstep_playback_requested"), TEXT("ambience_component_playing")})
                Passed &= Results->HasField(Check) && Results->GetBoolField(Check);
    }
    Results->SetBoolField(TEXT("passed"), Passed);
    Results->SetStringField(TEXT("error"), Error);
    Results->SetNumberField(TEXT("elapsed_wall_seconds"), FPlatformTime::Seconds() - Started);
    FString Json;
    FJsonSerializer::Serialize(Results.ToSharedRef(), TJsonWriterFactory<>::Create(&Json));
    if (!FFileHelper::SaveStringToFile(Json, *ReportPath, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) Passed = false;
    UE_LOG(LogTemp, Display, TEXT("REA_WORLD_SMOKE %s %s"), Passed ? TEXT("PASS") : TEXT("FAIL"), *ReportPath);
    FPlatformMisc::RequestExitWithStatus(false, Passed ? 0 : 1);
}

void AReaBullyWorldProbe::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (FPlatformTime::Seconds() - Started > 420.0) { Finish(TEXT("World integration phase timed out")); return; }
    AReaBullyWorldDirector* Director = nullptr;
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It) { Director = *It; break; }
    APlayerController* Controller = GetWorld()->GetFirstPlayerController();
    AReaBullyExplorer* Player = Controller ? Cast<AReaBullyExplorer>(Controller->GetPawn()) : nullptr;
    if ((Phase == 6 || Phase == 8) && Controller && TestedDoor.IsValid() && TestedDoor->GetWorld() == GetWorld())
    {
        if (bDoorKeyHeld) DoorKey(false);
        Results->SetStringField(TEXT("door_after_input"), TestedDoor->GetInteractionDiagnostics(Controller));
        Results->SetBoolField(Phase == 8 ? TEXT("dorm_door_keyboard_input") : TEXT("door_keyboard_input"), TestedDoor->GetToggleCount() > 0);
    }
    if (!Director || !Player || !Director->IsReady()) return;
    FString WindingProbeMesh;
    const bool WindingExperiment = FParse::Value(FCommandLine::Get(), TEXT("ReaBullyCollisionWindingProbe="), WindingProbeMesh);
    if (WindingExperiment && !bWindingProbeApplied)
    {
        UStaticMesh* Replacement = LoadObject<UStaticMesh>(nullptr, *WindingProbeMesh);
        if (!Replacement) { Finish(TEXT("Winding experiment mesh unavailable")); return; }
        int32 Changed = 0;
        for (TActorIterator<AReaBullyZoneGeometry> It(GetWorld()); It; ++It)
        {
            TArray<UHierarchicalInstancedStaticMeshComponent*> Components;
            It->GetComponents(Components);
            for (auto* Component : Components)
                if (Component->ComponentHasTag(TEXT("OriginalCOL3")) && Component->GetStaticMesh() &&
                    Component->GetStaticMesh()->GetPathName().Contains(TEXT("05140_fgrd_sc1b11")))
                {
                    Component->SetMobility(EComponentMobility::Movable);
                    if (Component->SetStaticMesh(Replacement)) ++Changed;
                    Component->SetMobility(EComponentMobility::Static);
                }
        }
        Results->SetStringField(TEXT("winding_probe_mesh"), WindingProbeMesh);
        Results->SetNumberField(TEXT("winding_probe_instances"), Changed);
        bWindingProbeApplied = true;
        if (Changed != 1) { Finish(TEXT("Winding experiment requires exactly one source ground instance")); return; }
        Director->RequestTravel(Director->Data->Start, TEXT("MainMap"));
        return;
    }
    PhaseTime += DeltaSeconds;
    if (Phase == 2)
        Results->SetNumberField(TEXT("measured_frames"), Results->GetNumberField(TEXT("measured_frames")) + 1);
    if (Phase == 0 && PhaseTime > 2.0f)
    {
        RecordPlayerState(TEXT("player_at_start"), Player);
        Results->SetBoolField(TEXT("world_streaming_ready"), Director->VisibleZones() > 0);
        Results->SetNumberField(TEXT("visible_zones"), Director->VisibleZones());
        const FFindFloorResult& Floor = Player->GetCharacterMovement()->CurrentFloor;
        Results->SetBoolField(TEXT("grounded_on_original_instances"), Player->GetCharacterMovement()->IsMovingOnGround()
            && Cast<UHierarchicalInstancedStaticMeshComponent>(Floor.HitResult.GetComponent()) != nullptr);
        Results->SetStringField(TEXT("floor_actor"), GetNameSafe(Floor.HitResult.GetActor()));
        Results->SetBoolField(TEXT("original_player_skin"), Player->GetMesh()->GetNumBones() == 36
            && Player->GetMesh()->GetSkeletalMeshAsset() &&
            Player->GetMesh()->GetSkeletalMeshAsset()->GetPathName().Contains(TEXT("JimmyActions/SK_Jimmy")));
        int32 Components = 0, Instances = 0, CollisionInstances = 0;
        for (TActorIterator<AReaBullyZoneGeometry> It(GetWorld()); It; ++It)
        {
            TArray<UHierarchicalInstancedStaticMeshComponent*> Shapes;
            It->GetComponents(Shapes);
            Components += Shapes.Num();
            for (auto* Shape : Shapes)
            {
                Instances += Shape->GetInstanceCount();
                if (Shape->ComponentHasTag(TEXT("OriginalCOL3"))) CollisionInstances += Shape->GetInstanceCount();
            }
        }
        Results->SetNumberField(TEXT("shape_components"), Components);
        Results->SetNumberField(TEXT("shape_instances"), Instances);
        Results->SetNumberField(TEXT("original_collision_instance_count"), CollisionInstances);
        Results->SetBoolField(TEXT("original_collision_instances"), CollisionInstances > 0);
        FString Screenshot;
        if (FParse::Value(FCommandLine::Get(), TEXT("ReaBullyScreenshot="), Screenshot))
            FScreenshotRequest::RequestScreenshot(Screenshot, true, false);
        StartPosition = Player->GetActorLocation();
        OriginalPlayer = Player;
        bWalkStarted = !FParse::Param(FCommandLine::Get(), TEXT("ReaBullyIntegrationProbe"));
        if (bWalkStarted) Key(true);
        ++Phase; PhaseTime = 0;
    }
    else if (Phase == 1)
    {
        if (!bWalkStarted)
        {
            if (PhaseTime < .25f) return; // Let the initial camera screenshot complete.
            // The integrated start faces a nearby original hangout. Walk away
            // from that stationary ped when testing movement and footsteps.
            Controller->SetControlRotation(FRotator(-8.0f, 0.0f, 0.0f));
            Key(true);
            bWalkStarted = true;
            PhaseTime = 0;
            return;
        }
        if (PhaseTime <= 1.2f) return;
        Key(false);
        RecordPlayerState(TEXT("player_after_walk"), Player);
        const float Distance = FVector::Dist2D(StartPosition, Player->GetActorLocation());
        Results->SetNumberField(TEXT("walked_cm"), Distance);
        Results->SetBoolField(TEXT("keyboard_moves_player"), Distance > 200.0f && Player->GetCharacterMovement()->IsMovingOnGround());
        if (WindingExperiment)
        {
            Results->SetBoolField(TEXT("winding_experiment_floor_and_walk_passed"), Distance > 200.0f &&
                Results->GetBoolField(TEXT("grounded_on_original_instances")) && Player->GetCharacterMovement()->IsMovingOnGround());
            Finish(TEXT("Diagnostic winding experiment only; full scene validation remains separate"));
            return;
        }
        Results->SetNumberField(TEXT("measurement_started_wall_seconds"), FPlatformTime::Seconds());
        Results->SetNumberField(TEXT("measured_frames"), 0);
        ++Phase; PhaseTime = 0;
    }
    else if (Phase == 2 && PhaseTime > 20.0f)
    {
        const double MeasuredSeconds = FPlatformTime::Seconds() - Results->GetNumberField(TEXT("measurement_started_wall_seconds"));
        Results->SetNumberField(TEXT("measured_wall_seconds"), MeasuredSeconds);
        Results->SetNumberField(TEXT("measured_simulation_seconds"), PhaseTime);
        Results->SetNumberField(TEXT("frames_per_wall_second"), Results->GetNumberField(TEXT("measured_frames")) / MeasuredSeconds);
        Results->RemoveField(TEXT("measurement_started_wall_seconds"));
        int32 Npcs = 0;
        bool OwnSkins = true, PatrolMoves = false, AllGrounded = true;
        TArray<TSharedPtr<FJsonValue>> Details;
        for (TActorIterator<AReaBullyAmbientPed> It(GetWorld()); It; ++It)
        {
            ++Npcs;
            OwnSkins &= It->Data && It->GetMesh()->GetNumBones() == 36 && !It->IsHidden()
                && It->GetMesh()->GetSkeletalMeshAsset() != Player->GetMesh()->GetSkeletalMeshAsset()
                && It->Data->Mesh == It->GetMesh()->GetSkeletalMeshAsset();
            PatrolMoves |= It->Route.Num() > 1 && It->DistanceWalked() > 100.0f;
            AllGrounded &= It->GetCharacterMovement()->IsMovingOnGround();
            auto Detail = MakeShared<FJsonObject>();
            Detail->SetNumberField(TEXT("ped_id"), It->Data ? It->Data->PedId : -1);
            Detail->SetNumberField(TEXT("distance_cm"), It->DistanceWalked());
            Detail->SetStringField(TEXT("source"), It->SourceReference);
            Detail->SetBoolField(TEXT("grounded"), It->GetCharacterMovement()->IsMovingOnGround());
            Detail->SetStringField(TEXT("position"), It->GetActorLocation().ToString());
            Details.Add(MakeShared<FJsonValueObject>(Detail));
        }
        Results->SetArrayField(TEXT("ambient_peds"), Details);
        Results->SetBoolField(TEXT("ambient_own_skins"), Npcs >= 2 && OwnSkins);
        Results->SetBoolField(TEXT("expanded_original_cast"), Npcs >= 7 && OwnSkins);
        Results->SetBoolField(TEXT("ambient_on_original_floor"), Npcs >= 7 && AllGrounded);
        Results->SetBoolField(TEXT("original_patrol_moves"), PatrolMoves);
        bool AudioAssets = false, Footsteps = false, Ambience = false;
        for (TActorIterator<AReaBullyWorldAudio> It(GetWorld()); It; ++It)
        {
            AudioAssets |= It->Data && It->Data->Waves.Num() == 207 && It->Data->FootstepPhases.Num() == 3;
            Footsteps |= It->GetFootstepsPlayed() >= 2;
            Ambience |= It->IsAmbiencePlaying();
            Results->SetNumberField(TEXT("footstep_requests"), It->GetFootstepsPlayed());
            Results->SetNumberField(TEXT("ambience_starts"), It->GetAmbienceStarts());
        }
        Results->SetBoolField(TEXT("recovered_audio_assets"), AudioAssets);
        Results->SetBoolField(TEXT("footstep_playback_requested"), Footsteps);
        Results->SetBoolField(TEXT("ambience_component_playing"), Ambience);
        Cast<AReaBullyPlayerController>(Controller)->ReturnToStart();
        ++Phase; PhaseTime = 0;
    }
    else if (Phase == 3 && PhaseTime > 1.0f)
    {
        RecordPlayerState(TEXT("player_after_reset"), Player);
        Results->SetBoolField(TEXT("reset_waits_for_original_floor"), Player != OriginalPlayer.Get()
            && Player->GetCharacterMovement()->IsMovingOnGround());
        if (!FParse::Param(FCommandLine::Get(), TEXT("ReaBullyIntegrationProbe"))) { Finish(); return; }
        for (TActorIterator<AReaBullyWorldDoor> It(GetWorld()); It; ++It)
            if (It->Data && !It->bHasTravelTarget && It->SourceAreaCode == 0 && It->GetLevel()->bIsVisible)
            {
                TestedDoor = *It;
                if (It->Data->ModelKey == TEXT("10030_escdoorr")) break;
            }
        if (!TestedDoor.IsValid()) { Finish(TEXT("No original nontravel door in streamed world")); return; }
        ClosedDoorBones.Reset();
        for (int32 Index = 0; Index < TestedDoor->MeshComponent->GetNumBones(); ++Index)
            ClosedDoorBones.Add(TestedDoor->MeshComponent->GetBoneTransform(Index));
        Results->SetStringField(TEXT("animated_door_model"), TestedDoor->Data->ModelKey);
        if (!TestedDoor->RequestToggle()) { Finish(TEXT("Door OPEN rejected")); return; }
        ++Phase; PhaseTime = 0;
    }
    else if (Phase == 4 && TestedDoor.IsValid() && !TestedDoor->IsAnimating() && PhaseTime > .1f)
    {
        bool Changed = false;
        for (int32 Index = 0; Index < ClosedDoorBones.Num(); ++Index)
            Changed |= !ClosedDoorBones[Index].Equals(TestedDoor->MeshComponent->GetBoneTransform(Index), .01f);
        Results->SetBoolField(TEXT("original_door_pose_changes"), Changed);
        if (!TestedDoor->IsOpen() || !Changed || !TestedDoor->RequestToggle())
            { Finish(TEXT("Door original OPEN pose or CLOSE request failed")); return; }
        ++Phase; PhaseTime = 0;
    }
    else if (Phase == 5 && TestedDoor.IsValid() && !TestedDoor->IsAnimating() && PhaseTime > .1f)
    {
        Results->SetBoolField(TEXT("original_door_open_close"), !TestedDoor->IsOpen() && TestedDoor->GetToggleCount() >= 2);
        TestedDoor.Reset();
        for (TActorIterator<AReaBullyWorldDoor> It(GetWorld()); It; ++It)
            if (It->Data && It->bHasTravelTarget && It->SourceAreaCode == 0 &&
                It->SourceName == TEXT("DT_tschool_SchoolFrontDoorL") &&
                It->TravelArea == TEXT("School_Hallways") && It->GetLevel()->bIsVisible)
                { TestedDoor = *It; break; }
        if (!TestedDoor.IsValid()) { Finish(TEXT("Original school transition is unavailable")); return; }
        ExpectedTravelArea = TestedDoor->TravelArea;
        const FVector Extent = TestedDoor->ClosedBlocker->GetUnscaledBoxExtent();
        FVector Normal = Extent.X < Extent.Y ? FVector::ForwardVector : FVector::RightVector;
        Normal = TestedDoor->ClosedBlocker->GetComponentTransform().TransformVectorNoScale(Normal);
        const FVector Position = TestedDoor->ClosedBlocker->GetComponentLocation() + Normal * 140.0f;
        Player->SetActorLocation(Position, false, nullptr, ETeleportType::TeleportPhysics);
        Player->GetCharacterMovement()->StopMovementImmediately();
        DoorKey(true);
        Results->SetStringField(TEXT("door_at_key_press"), TestedDoor->GetInteractionDiagnostics(Controller));
        RecordPlayerState(TEXT("player_at_door_key"), Player);
        ++Phase; PhaseTime = 0;
    }
    else if (Phase == 6)
    {
        if (bDoorKeyHeld) DoorKey(false);
        if (TestedDoor.IsValid() && TestedDoor->GetToggleCount() > 0)
            Results->SetBoolField(TEXT("door_keyboard_input"), true);
        if (Director->GetActiveArea() == ExpectedTravelArea && Player->GetCharacterMovement()->IsMovingOnGround())
        {
            Results->SetBoolField(TEXT("door_travel_arrives_in_area"), true);
            Results->SetStringField(TEXT("entered_original_area"), Director->GetActiveArea().ToString());
            Cast<AReaBullyPlayerController>(Controller)->ReturnToStart();
            ++Phase; PhaseTime = 0;
        }
        else if (PhaseTime > 15.0f)
        {
            RecordPlayerState(TEXT("player_at_door_timeout"), Player);
            Finish(TEXT("E input did not enter the original school interior")); return;
        }
    }
    else if (Phase == 7 && PhaseTime > 1.0f)
    {
        Results->SetBoolField(TEXT("return_from_interior"), Director->GetActiveArea() == TEXT("MainMap")
            && Player->GetCharacterMovement()->IsMovingOnGround());
        TestedDoor.Reset();
        for (TActorIterator<AReaBullyWorldDoor> It(GetWorld()); It; ++It)
            if (It->bHasTravelTarget && It->SourceName == TEXT("DT_tschool_BoysDormL") &&
                It->TravelArea == TEXT("Boys_Dorm") && It->GetLevel()->bIsVisible)
                { TestedDoor = *It; break; }
        if (!TestedDoor.IsValid()) { Finish(TEXT("Original dormitory transition unavailable")); return; }
        ExpectedTravelArea = TestedDoor->TravelArea;
        const FVector Extent = TestedDoor->ClosedBlocker->GetUnscaledBoxExtent();
        FVector Normal = Extent.X < Extent.Y ? FVector::ForwardVector : FVector::RightVector;
        Normal = TestedDoor->ClosedBlocker->GetComponentTransform().TransformVectorNoScale(Normal);
        Player->SetActorLocation(TestedDoor->ClosedBlocker->GetComponentLocation() + Normal * 140.0f,
            false, nullptr, ETeleportType::TeleportPhysics);
        Player->GetCharacterMovement()->StopMovementImmediately();
        DoorKey(true);
        ++Phase; PhaseTime = 0;
    }
    else if (Phase == 8)
    {
        if (bDoorKeyHeld) DoorKey(false);
        if (Director->GetActiveArea() == ExpectedTravelArea && Player->GetCharacterMovement()->IsMovingOnGround())
        {
            Results->SetBoolField(TEXT("dorm_travel_arrives_in_area"), true);
            Cast<AReaBullyPlayerController>(Controller)->ReturnToStart();
            ++Phase; PhaseTime = 0;
        }
        else if (PhaseTime > 15.0f) { Finish(TEXT("E input did not enter the original dormitory")); return; }
    }
    else if (Phase == 9 && PhaseTime > 1.0f)
    {
        Results->SetBoolField(TEXT("return_from_dorm"), Director->GetActiveArea() == TEXT("MainMap")
            && Player->GetCharacterMovement()->IsMovingOnGround());
        Finish();
    }
}
