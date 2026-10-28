#include "ReaBullyExploration.h"
#include "ReaBullySmokeProbe.h"
#include "ReaBullyAnimInstance.h"
#include "ReaBullyWorld.h"
#include "ReaBullyWorldProbe.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Animation/BlendSpace1D.h"
#include "Camera/CameraComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/InputComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Engine/SkeletalMesh.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/SpringArmComponent.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Modules/ModuleManager.h"

IMPLEMENT_PRIMARY_GAME_MODULE(FDefaultGameModuleImpl, ReaBullyRuntime, "ReaBullyRuntime");

AReaBullyExplorer::AReaBullyExplorer()
{
    GetCapsuleComponent()->InitCapsuleSize(34.0f, 88.0f);
    PrimaryActorTick.bCanEverTick = true;
    CameraArm = CreateDefaultSubobject<USpringArmComponent>(TEXT("CameraArm"));
    CameraArm->SetupAttachment(GetCapsuleComponent());
    CameraArm->TargetArmLength = 350.0f;
    CameraArm->TargetOffset = FVector(0.0, 0.0, 35.0);
    CameraArm->bUsePawnControlRotation = true;
    CameraArm->bDoCollisionTest = true;
    Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
    Camera->SetupAttachment(CameraArm, USpringArmComponent::SocketName);
    Camera->FieldOfView = 75.0f;
    UCharacterMovementComponent* Movement = GetCharacterMovement();
    // C_PLAYER ARROW displacement / exact MAINPED.HXD duration, metres -> cm.
    Movement->MaxWalkSpeed = 361.375f;
    Movement->RotationRate = FRotator(0.0, 540.0, 0.0);
    Movement->JumpZVelocity = 520.0f;
    Movement->GravityScale = 1.4f;
    Movement->AirControl = 0.15f;
    Movement->MaxStepHeight = 40.0f;
    Movement->BrakingDecelerationWalking = 2000.0f;
}

void AReaBullyExplorer::BeginPlay()
{
    Super::BeginPlay();
    USkeletalMesh* Jimmy = LoadObject<USkeletalMesh>(nullptr, TEXT("/Game/Original/Jimmy/SK_Jimmy.SK_Jimmy"));
    Locomotion = LoadObject<UBlendSpace1D>(nullptr, TEXT("/Game/Original/Jimmy/BS_Locomotion.BS_Locomotion"));
    Actions = LoadObject<UReaBullyPlayerActions>(nullptr, TEXT("/Game/Original/JimmyActions/DA_PlayerActions.DA_PlayerActions"));
    if (Actions && Actions->Mesh && Actions->Locomotion)
    {
        Jimmy = Actions->Mesh;
        Locomotion = Actions->Locomotion;
    }
    if (Jimmy && Locomotion && Jimmy->GetSkeleton() == Locomotion->GetSkeleton())
    {
        GetMesh()->SetSkeletalMeshAsset(Jimmy);
        // C_PLAYER animated forward is NIF +Y -> glTF -Z -> Unreal -Y.
        // This differs from the static bind pose; preserve the original curves.
        GetMesh()->SetRelativeRotation(FRotator(0.0, 90.0, 0.0));
        const FBoxSphereBounds Bounds = Jimmy->GetBounds();
        GetMesh()->SetRelativeLocation(FVector(0.0, 0.0,
            -GetCapsuleComponent()->GetScaledCapsuleHalfHeight() - (Bounds.Origin.Z - Bounds.BoxExtent.Z)));
        GetMesh()->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        GetMesh()->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
        if (Actions)
        {
            GetMesh()->SetAnimInstanceClass(UReaBullyAnimInstance::StaticClass());
            GetMesh()->GetSingleNodeInstance()->SetAnimationAsset(Locomotion, true);
        }
        else GetMesh()->PlayAnimation(Locomotion, true);
    }
    else
    {
        UE_LOG(LogTemp, Error, TEXT("Recovered Jimmy mesh/locomotion missing or incompatible"));
        bFirstPerson = true;
    }
    ApplyView();
}

void AReaBullyExplorer::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (UAnimSingleNodeInstance* Animation = GetMesh()->GetSingleNodeInstance())
        Animation->SetBlendSpacePosition(FVector(GetVelocity().Size2D(), 0.0, 0.0));
    UpdateAction(DeltaSeconds);
    // Running jump poses extend below the bind pose. Give the complete body room in frame.
    const bool bActionView = ActionPhase != EReaBullyActionPhase::Locomotion;
    CameraArm->TargetArmLength = FMath::FInterpTo(CameraArm->TargetArmLength, bActionView ? 390.0f : 350.0f, DeltaSeconds, 7.0f);
    CameraArm->TargetOffset.Z = FMath::FInterpTo(CameraArm->TargetOffset.Z, bActionView ? 15.0 : 35.0, DeltaSeconds, 7.0f);
}

void AReaBullyExplorer::ToggleView()
{
    bFirstPerson = !bFirstPerson;
    ApplyView();
}

void AReaBullyExplorer::ApplyView()
{
    bUseControllerRotationYaw = bFirstPerson;
    GetCharacterMovement()->bOrientRotationToMovement = !bFirstPerson;
    GetMesh()->SetOwnerNoSee(bFirstPerson);
    Camera->AttachToComponent(bFirstPerson ? static_cast<USceneComponent*>(GetCapsuleComponent()) : CameraArm.Get(),
        FAttachmentTransformRules::SnapToTargetNotIncludingScale,
        bFirstPerson ? NAME_None : USpringArmComponent::SocketName);
    Camera->SetRelativeLocation(bFirstPerson ? FVector(0.0, 0.0, 64.0) : FVector::ZeroVector);
    Camera->SetRelativeRotation(FRotator::ZeroRotator);
    Camera->bUsePawnControlRotation = bFirstPerson;
    Camera->FieldOfView = bFirstPerson ? 85.0f : 75.0f;
}

void AReaBullyExplorer::SetupPlayerInputComponent(UInputComponent* Input)
{
    Super::SetupPlayerInputComponent(Input);
    Input->BindAxis(TEXT("MoveForward"), this, &AReaBullyExplorer::MoveForward);
    Input->BindAxis(TEXT("MoveRight"), this, &AReaBullyExplorer::MoveRight);
    Input->BindAxis(TEXT("Turn"), this, &APawn::AddControllerYawInput);
    Input->BindAxis(TEXT("LookUp"), this, &APawn::AddControllerPitchInput);
    Input->BindAction(TEXT("Jump"), IE_Pressed, this, &AReaBullyExplorer::RequestJump);
    Input->BindAction(TEXT("Jump"), IE_Released, this, &ACharacter::StopJumping);
    Input->BindAction(TEXT("Sprint"), IE_Pressed, this, &AReaBullyExplorer::StartSprint);
    Input->BindAction(TEXT("Sprint"), IE_Released, this, &AReaBullyExplorer::StopSprint);
    Input->BindAction(TEXT("ToggleView"), IE_Pressed, this, &AReaBullyExplorer::ToggleView);
}

void AReaBullyExplorer::MoveForward(float Value)
{
    if (Controller && Value != 0.0f)
        AddMovementInput(FRotationMatrix(FRotator(0.0, Controller->GetControlRotation().Yaw, 0.0)).GetUnitAxis(EAxis::X), Value);
}

void AReaBullyExplorer::MoveRight(float Value)
{
    if (Controller && Value != 0.0f)
        AddMovementInput(FRotationMatrix(FRotator(0.0, Controller->GetControlRotation().Yaw, 0.0)).GetUnitAxis(EAxis::Y), Value);
}

void AReaBullyExplorer::StartSprint() { bSprintRequested = true; GetCharacterMovement()->MaxWalkSpeed = 801.2f; }
void AReaBullyExplorer::StopSprint() { bSprintRequested = false; GetCharacterMovement()->MaxWalkSpeed = 361.375f; }

void AReaBullyExplorer::FellOutOfWorld(const UDamageType& DamageType)
{
    if (AReaBullyPlayerController* Player = Cast<AReaBullyPlayerController>(Controller))
        Player->ReturnToStart();
    else
        Super::FellOutOfWorld(DamageType);
}

void AReaBullyPlayerController::BeginPlay()
{
    Super::BeginPlay();
    SetInputMode(FInputModeGameOnly());
    bShowMouseCursor = false;
    FRotator View = GetControlRotation();
    View.Pitch = -8.0f;
    SetControlRotation(View);
}

void AReaBullyPlayerController::SetupInputComponent()
{
    Super::SetupInputComponent();
    InputComponent->BindAction(TEXT("ReturnToStart"), IE_Pressed, this, &AReaBullyPlayerController::ReturnToStart);
    InputComponent->BindAction(TEXT("ExitExploration"), IE_Pressed, this, &AReaBullyPlayerController::ExitExploration);
}

void AReaBullyPlayerController::ReturnToStart()
{
    APawn* PreviousPawn = GetPawn();
    if (PreviousPawn)
    {
        UnPossess();
        PreviousPawn->Destroy();
    }
    if (AGameModeBase* Mode = GetWorld()->GetAuthGameMode())
        Mode->RestartPlayer(this);
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It)
        if (It->Data) It->RequestTravel(It->Data->Start, TEXT("MainMap"));
}

void AReaBullyPlayerController::ExitExploration()
{
    UKismetSystemLibrary::QuitGame(this, this, EQuitPreference::Quit, false);
}

void AReaBullyExplorationHUD::DrawHUD()
{
    Super::DrawHUD();
    if (!Canvas || !GEngine) return;
    const float Scale = FMath::Clamp(Canvas->SizeX / 1600.0f, 0.75f, 1.5f);
    const float Margin = 26.0f * Scale;
    DrawRect(FLinearColor(0.02f, 0.025f, 0.03f, 0.7f), Margin - 10.0f, Margin - 8.0f, 320.0f * Scale, 61.0f * Scale);
    AReaBullyWorldDirector* Director = nullptr;
    for (TActorIterator<AReaBullyWorldDirector> It(GetWorld()); It; ++It) { Director = *It; break; }
    DrawText(Director ? TEXT("BULLWORTH") : TEXT("BULLWORTH / SCUOLA"), FLinearColor::White,
        Margin, Margin, GEngine->GetMediumFont(), Scale);
    const TCHAR* AreaTitle = TEXT("Esplorazione");
    if (Director)
    {
        if (Director->GetActiveArea() == TEXT("MainMap")) AreaTitle = TEXT("Campus - esplorazione");
        else if (Director->GetActiveArea() == TEXT("School_Hallways")) AreaTitle = TEXT("Scuola - corridoi");
        else if (Director->GetActiveArea() == TEXT("Boys_Dorm")) AreaTitle = TEXT("Dormitorio");
    }
    DrawText(AreaTitle, FLinearColor(0.7f, 0.77f, 0.8f), Margin, Margin + 26.0f * Scale, GEngine->GetSmallFont(), Scale);
    DrawRect(FLinearColor(0.02f, 0.025f, 0.03f, 0.7f), Margin - 10.0f, Canvas->SizeY - 56.0f * Scale, 1050.0f * Scale, 36.0f * Scale);
    DrawText(TEXT("WASD  muoviti    Mouse  guarda    E  porte    Spazio  salta    Shift  corri    V  visuale    R  ritorna    Esc  esci"),
             FLinearColor::White, Margin, Canvas->SizeY - 49.0f * Scale, GEngine->GetSmallFont(), Scale);
    if (Director && !Director->IsReady())
    {
        DrawRect(FLinearColor(0.02f, 0.025f, 0.03f, .85f), 0, Canvas->SizeY * .42f, Canvas->SizeX, 70.0f * Scale);
        DrawText(TEXT("Caricamento dell'area..."), FLinearColor::White, Canvas->SizeX * .38f,
            Canvas->SizeY * .44f, GEngine->GetMediumFont(), Scale);
    }
}

AReaBullyGameMode::AReaBullyGameMode()
{
    DefaultPawnClass = AReaBullyExplorer::StaticClass();
    PlayerControllerClass = AReaBullyPlayerController::StaticClass();
    HUDClass = AReaBullyExplorationHUD::StaticClass();
}

void AReaBullyGameMode::StartPlay()
{
    Super::StartPlay();
#if !UE_BUILD_SHIPPING
    FString Report;
    if (FParse::Value(FCommandLine::Get(), TEXT("ReaBullyWorldReport="), Report))
    {
        AReaBullyWorldProbe* Probe = GetWorld()->SpawnActor<AReaBullyWorldProbe>();
        Probe->ReportPath = Report;
    }
    if (FParse::Value(FCommandLine::Get(), TEXT("ReaBullySmokeReport="), Report))
    {
        AReaBullySmokeProbe* Probe = GetWorld()->SpawnActor<AReaBullySmokeProbe>();
        Probe->ReportPath = Report;
    }
#endif
}
