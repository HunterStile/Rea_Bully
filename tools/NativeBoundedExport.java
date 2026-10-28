// Complementary Ghidra API export with a real per-function deadline.
// @category Bully
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.framework.Application;
import com.google.gson.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.io.BufferedWriter;

public class NativeBoundedExport extends GhidraScript {
    private static final Gson JSON = new GsonBuilder().serializeNulls().create();
    private static String sha(byte[] data) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(data));
    }
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length != 4) throw new IllegalArgumentException("output addresses expected-sha timeout-seconds");
        Path output = Path.of(args[0]);
        JsonArray addresses = JsonParser.parseString(Files.readString(Path.of(args[1]))).getAsJsonArray();
        String expected = args[2];
        int timeout = Integer.parseInt(args[3]);
        if (!expected.equals(currentProgram.getExecutableSHA256())) throw new IllegalStateException("Input SHA mismatch");
        Files.createDirectories(output.resolve("functions"));
        Files.createDirectories(output.resolve("assembly"));
        DecompInterface decompiler = new DecompInterface();
        DecompileOptions options = new DecompileOptions();
        options.grabFromProgram(currentProgram);
        decompiler.setOptions(options);
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(true);
        decompiler.toggleJumpLoads(true);
        if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException("Decompiler open failed");
        try (BufferedWriter index = Files.newBufferedWriter(output.resolve("functions.jsonl"), StandardCharsets.UTF_8,
                                                            StandardOpenOption.CREATE_NEW)) {
            int number = 0;
            for (JsonElement selected : addresses) {
                monitor.checkCancelled();
                String requested = selected.getAsString();
                JsonObject entry = new JsonObject();
                entry.addProperty("address", requested);
                entry.addProperty("timeout_seconds", timeout);
                entry.addProperty("ghidra_version", Application.getApplicationVersion());
                long start = System.nanoTime();
                Function function = null;
                try {
                    Address address = toAddr(requested);
                    function = currentProgram.getFunctionManager().getFunctionAt(address);
                    if (function == null) {
                        Function owner = currentProgram.getFunctionManager().getFunctionContaining(address);
                        entry.addProperty("status", "missing_exact_entry");
                        entry.addProperty("containing_address", owner == null ? null : "0x" + owner.getEntryPoint().toString());
                    } else {
                        entry.addProperty("canonical_address", "0x" + function.getEntryPoint().toString());
                        entry.addProperty("provider_name", function.getName());
                        entry.addProperty("body_bytes", function.getBody().getNumAddresses());
                        DecompileResults result = decompiler.decompileFunction(function, timeout, monitor);
                        if (result.decompileCompleted() && result.getDecompiledFunction() != null) {
                            String code = result.getDecompiledFunction().getC().replace("\r\n", "\n").replace("\r", "\n");
                            String header = "/* Ghidra public API complementary pseudocode; not original source or REA Evidence.\n" +
                                " * Input SHA-256: " + expected + "\n * Requested address: " + requested + "\n */\n\n";
                            Path target = output.resolve("functions").resolve(requested + ".c");
                            byte[] bytes = (header + code).getBytes(StandardCharsets.UTF_8);
                            Files.write(target, bytes, StandardOpenOption.CREATE_NEW);
                            entry.addProperty("status", "ok");
                            entry.addProperty("file", output.relativize(target).toString());
                            entry.addProperty("sha256", sha(bytes));
                            entry.addProperty("bytes", bytes.length);
                        } else {
                            entry.addProperty("status", result.isTimedOut() ? "timeout" : "decompile_failed");
                            entry.addProperty("error", result.getErrorMessage());
                        }
                    }
                } catch (Exception error) {
                    entry.addProperty("status", "exception");
                    entry.addProperty("error", error.toString());
                }
                if (!"ok".equals(entry.get("status").getAsString()) && function != null) {
                    Path target = output.resolve("assembly").resolve(requested + ".asm");
                    try (BufferedWriter assembly = Files.newBufferedWriter(target, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW)) {
                        InstructionIterator instructions = currentProgram.getListing().getInstructions(function.getBody(), true);
                        while (instructions.hasNext()) {
                            Instruction instruction = instructions.next();
                            assembly.write("0x" + instruction.getAddress().toString() + " " + HexFormat.of().formatHex(instruction.getBytes()) + " " + instruction.toString() + "\n");
                        }
                    }
                    entry.addProperty("assembly_file", output.relativize(target).toString());
                    entry.addProperty("assembly_sha256", sha(Files.readAllBytes(target)));
                    decompiler.resetDecompiler();
                }
                entry.addProperty("elapsed_seconds", (System.nanoTime() - start) / 1_000_000_000.0);
                index.write(JSON.toJson(entry)); index.newLine(); index.flush();
                if (++number % 64 == 0) println("Bully bounded export: " + number + "/" + addresses.size());
            }
        } finally {
            decompiler.dispose();
        }
    }
}
