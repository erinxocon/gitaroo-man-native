// Writes Ghidra pseudo-C for every function (or a given list of addresses) to an output file.
// Args: <output.c> [addr ...]
// @category GitarooNative

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;

import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.List;

public class DumpDecompilation extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        List<Function> functions = new ArrayList<>();
        if (args.length > 1) {
            for (int i = 1; i < args.length; i++) {
                Function func = getFunctionContaining(toAddr(Long.decode(args[i])));
                if (func != null) {
                    functions.add(func);
                }
            }
        } else {
            for (Function func : currentProgram.getFunctionManager().getFunctions(true)) {
                functions.add(func);
            }
        }

        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        int ok = 0, failed = 0;
        try (PrintWriter out = new PrintWriter(args[0])) {
            for (Function func : functions) {
                if (monitor.isCancelled()) {
                    break;
                }
                out.printf("// ===== %s @ %s =====%n", func.getName(), func.getEntryPoint());
                DecompileResults res = decomp.decompileFunction(func, 60, monitor);
                if (res != null && res.decompileCompleted()) {
                    out.println(res.getDecompiledFunction().getC());
                    ok++;
                } else {
                    out.println("// decompile failed: " + (res == null ? "null" : res.getErrorMessage()));
                    failed++;
                }
            }
        }
        decomp.dispose();
        println(String.format("DumpDecompilation: ok=%d failed=%d", ok, failed));
    }
}
