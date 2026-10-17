// Applies "name 0xADDR" lines as function names, creating functions where missing.
// @category GitarooNative

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.SourceType;

import java.io.BufferedReader;
import java.io.FileReader;

public class ApplySymbols extends GhidraScript {
    @Override
    public void run() throws Exception {
        String path = getScriptArgs()[0];
        int renamed = 0, created = 0, failed = 0;
        try (BufferedReader reader = new BufferedReader(new FileReader(path))) {
            String line;
            while ((line = reader.readLine()) != null) {
                String[] parts = line.trim().split("\\s+");
                if (parts.length < 2 || parts[0].startsWith("#")) {
                    continue;
                }
                Address addr = toAddr(Long.decode(parts[1]));
                Function func = getFunctionAt(addr);
                if (func == null) {
                    disassemble(addr);
                    func = createFunction(addr, parts[0]);
                    if (func == null) {
                        failed++;
                        println("could not create function at " + addr + " for " + parts[0]);
                        continue;
                    }
                    created++;
                }
                func.setName(parts[0], SourceType.USER_DEFINED);
                renamed++;
            }
        }
        println(String.format("ApplySymbols: renamed=%d created=%d failed=%d", renamed, created, failed));
    }
}
