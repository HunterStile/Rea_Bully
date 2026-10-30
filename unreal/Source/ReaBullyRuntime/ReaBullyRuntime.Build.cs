using UnrealBuildTool;

public class ReaBullyRuntime : ModuleRules
{
    public ReaBullyRuntime(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "InputCore" });
        PrivateDependencyModuleNames.AddRange(new[] { "Json" });
        AddEngineThirdPartyPrivateStaticDependencies(Target, "OpenSSL");
    }
}
