#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "GameFramework/Actor.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "ReaBullyWorldProp.generated.h"

class USkeletalMesh;
class UStaticMesh;
class USkeletalMeshComponent;
class UAnimSequence;

/** One original object skin, with at most one explicitly selected ambient clip. */
UCLASS(BlueprintType)
class REABULLYRUNTIME_API UReaBullyWorldPropData : public UDataAsset
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere) FString ModelKey;
    UPROPERTY(EditAnywhere) FString SourceSHA256;
    UPROPERTY(EditAnywhere) TObjectPtr<USkeletalMesh> Mesh;
    UPROPERTY(EditAnywhere) TObjectPtr<UAnimSequence> AmbientLoop;
    UPROPERTY(EditAnywhere) FString AmbientLoopOriginal;
};

/** No synthetic physics or event animations: source bind pose unless approved. */
UCLASS()
class REABULLYRUNTIME_API AReaBullyWorldProp : public AActor
{
    GENERATED_BODY()
public:
    AReaBullyWorldProp();
    UPROPERTY(VisibleAnywhere) TObjectPtr<USkeletalMeshComponent> MeshComponent;
    UPROPERTY(EditAnywhere) TObjectPtr<UReaBullyWorldPropData> Data;
    UPROPERTY(EditAnywhere) FString SourceReference;
    UFUNCTION(BlueprintCallable, CallInEditor, Category="Recovered world")
    bool ConfigureRecoveredProp(UReaBullyWorldPropData* Source);
    virtual void OnConstruction(const FTransform& Transform) override;
    virtual void BeginPlay() override;
};

/** Reads the imported mesh's own reference skeleton for the importer audit. */
UCLASS()
class REABULLYRUNTIME_API UReaBullyWorldPropTools : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
public:
    UFUNCTION(BlueprintCallable, Category="Recovered assets")
    static TArray<FString> GetMeshBoneNames(USkeletalMesh* Mesh);
    UFUNCTION(BlueprintCallable, Category="Recovered assets")
    static TArray<int32> GetMeshBoneParents(USkeletalMesh* Mesh);
    /** Read-only asset diagnostics; does not initialize a world or cook physics. */
    UFUNCTION(BlueprintCallable, Category="Recovered assets")
    static FString InspectCollisionMesh(UStaticMesh* Mesh);
};
