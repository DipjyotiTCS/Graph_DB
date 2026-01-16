package com.supergraph;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.*;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.ast.type.ClassOrInterfaceType;
import com.github.javaparser.resolution.SymbolResolver;
import com.github.javaparser.resolution.declarations.ResolvedMethodDeclaration;
import com.github.javaparser.resolution.types.ResolvedType;
import com.github.javaparser.symbolsolver.JavaSymbolSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.*;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.stream.Collectors;

public class SemanticParserCli {

    public static class Graph {
        public String project_name;
        public String repo_id;
        public List<Map<String, Object>> types = new ArrayList<>();
        public List<Map<String, Object>> methods = new ArrayList<>();
        public List<Map<String, Object>> fields = new ArrayList<>();
        public List<Map<String, Object>> dependencies = new ArrayList<>();
        public List<Map<String, Object>> extends_rel = new ArrayList<>();
        public List<Map<String, Object>> implements_rel = new ArrayList<>();
        public List<Map<String, Object>> calls = new ArrayList<>();
    }

    public static void main(String[] args) throws Exception {
        Map<String, String> a = parseArgs(args);
        String root = require(a, "root");
        String projectName = a.getOrDefault("projectName", new File(root).getName());
        String repoId = a.getOrDefault("repoId", "local");
        String out = a.get("out"); // optional path; if absent -> stdout

        Path rootPath = Paths.get(root).toAbsolutePath().normalize();

        // Find likely source roots (maven/gradle) but also allow parsing any java under root.
        List<Path> sourceRoots = detectSourceRoots(rootPath);
        if (sourceRoots.isEmpty()) sourceRoots = List.of(rootPath);

        CombinedTypeSolver typeSolver = new CombinedTypeSolver();
        typeSolver.add(new ReflectionTypeSolver());

        for (Path sr : sourceRoots) {
            if (Files.isDirectory(sr)) {
                typeSolver.add(new JavaParserTypeSolver(sr.toFile()));
            }
        }

        JavaSymbolSolver solver = new JavaSymbolSolver(typeSolver);
        ParserConfiguration cfg = new ParserConfiguration()
                .setSymbolResolver(solver)
                .setCharacterEncoding(StandardCharsets.UTF_8);

        StaticJavaParser.setConfiguration(cfg);

        // Parse all java files
        List<Path> javaFiles = findJavaFiles(rootPath);

        // First pass: collect internal types (FQNs)
        Map<String, TypeMeta> internalTypes = new LinkedHashMap<>();
        Map<Path, CompilationUnit> units = new LinkedHashMap<>();

        for (Path jf : javaFiles) {
            try {
                CompilationUnit cu = StaticJavaParser.parse(jf);
                units.put(jf, cu);
                cu.findAll(TypeDeclaration.class).forEach(td -> {
                    String fqn = getFqn(cu, td);
                    if (fqn != null && !fqn.isBlank()) {
                        internalTypes.putIfAbsent(fqn, new TypeMeta(fqn, td.getNameAsString(), rootPath.relativize(jf).toString()));
                    }
                });
            } catch (Exception ex) {
                // skip unparsable file; still continue
            }
        }

        Set<String> internalFqns = internalTypes.keySet();

        Graph g = new Graph();
        g.project_name = projectName;
        g.repo_id = repoId;

        // Emit types
        for (TypeMeta tm : internalTypes.values()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("project_name", projectName);
            row.put("repo_id", repoId);
            row.put("fqn", tm.fqn);
            row.put("name", tm.name);
            row.put("file", tm.file);
            row.put("file_hash", sha1(readBytesSafe(rootPath.resolve(tm.file))));
            g.types.add(row);
        }

        // Second pass: methods, fields, semantic relationships
        for (Map.Entry<Path, CompilationUnit> e : units.entrySet()) {
            Path file = e.getKey();
            CompilationUnit cu = e.getValue();
            String rel = rootPath.relativize(file).toString();

            for (TypeDeclaration<?> td : cu.findAll(TypeDeclaration.class)) {
                if (!(td instanceof ClassOrInterfaceDeclaration || td instanceof EnumDeclaration || td instanceof RecordDeclaration)) {
                    continue;
                }

                String ownerFqn = getFqn(cu, td);
                if (ownerFqn == null || !internalFqns.contains(ownerFqn)) continue;

                // extends / implements (semantic best-effort)
                if (td instanceof ClassOrInterfaceDeclaration) {
                    ClassOrInterfaceDeclaration cid = (ClassOrInterfaceDeclaration) td;
                    for (ClassOrInterfaceType ext : cid.getExtendedTypes()) {
                        String target = resolveTypeFqn(ext, internalFqns);
                        if (target != null) g.extends_rel.add(relPair(projectName, repoId, ownerFqn, target));
                    }
                    for (ClassOrInterfaceType impl : cid.getImplementedTypes()) {
                        String target = resolveTypeFqn(impl, internalFqns);
                        if (target != null) g.implements_rel.add(relPair(projectName, repoId, ownerFqn, target));
                    }
                }

                // fields
                for (FieldDeclaration fd : td.getFields()) {
                    for (VariableDeclarator var : fd.getVariables()) {
                        String fname = var.getNameAsString();
                        String ftype = safeDescribeType(var.getType(), internalFqns);
                        Map<String, Object> row = new LinkedHashMap<>();
                        row.put("project_name", projectName);
                        row.put("repo_id", repoId);
                        row.put("owner_fqn", ownerFqn);
                        row.put("name", fname);
                        row.put("type", ftype);
                        g.fields.add(row);

                        String dep = extractInternalFromTypeString(ftype, internalFqns);
                        if (dep != null && !dep.equals(ownerFqn)) {
                            g.dependencies.add(depEdge(projectName, repoId, ownerFqn, dep, "field", rel));
                        }
                    }
                }

                // methods
                List<CallableDeclaration<?>> callables = new ArrayList<>();
                td.getMembers().forEach(m -> {
                    if (m instanceof MethodDeclaration) callables.add((MethodDeclaration)m);
                    if (m instanceof ConstructorDeclaration) callables.add((ConstructorDeclaration)m);
                });

                for (CallableDeclaration<?> md : callables) {
                    String mName = (md instanceof ConstructorDeclaration) ? td.getNameAsString() : ((MethodDeclaration) md).getNameAsString();
                    List<Map<String, String>> params = new ArrayList<>();
                    List<String> paramTypes = new ArrayList<>();
                    for (Parameter p : md.getParameters()) {
                        String pt = safeDescribeType(p.getType(), internalFqns);
                        params.add(Map.of("name", p.getNameAsString(), "type", pt));
                        paramTypes.add(pt);
                        String dep = extractInternalFromTypeString(pt, internalFqns);
                        if (dep != null && !dep.equals(ownerFqn)) {
                            g.dependencies.add(depEdge(projectName, repoId, ownerFqn, dep, "param", rel));
                        }
                    }
                    String signature = mName + "(" + String.join(",", paramTypes) + ")";

                    String returnType = "void";
                    if (md instanceof MethodDeclaration) {
                        returnType = safeDescribeType(((MethodDeclaration) md).getType(), internalFqns);
                        String dep = extractInternalFromTypeString(returnType, internalFqns);
                        if (dep != null && !dep.equals(ownerFqn)) {
                            g.dependencies.add(depEdge(projectName, repoId, ownerFqn, dep, "return", rel));
                        }
                    }

                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("project_name", projectName);
                    row.put("repo_id", repoId);
                    row.put("owner_fqn", ownerFqn);
                    row.put("name", mName);
                    row.put("signature", signature);
                    row.put("returnType", returnType);
                    row.put("params", params);
                    row.put("file", rel);
                    if (md.getRange().isPresent()) {
                        row.put("beginLine", md.getRange().get().begin.line);
                        row.put("endLine", md.getRange().get().end.line);
                    }
                    // hash of method/ctor body (semantic diffing of business logic)
                    String bodyText = "";
                    try {
                        if (md instanceof MethodDeclaration) {
                            MethodDeclaration md2 = (MethodDeclaration) md;
                            bodyText = md2.getBody().map(Object::toString).orElse("");
                        } else if (md instanceof ConstructorDeclaration) {
                            ConstructorDeclaration cd2 = (ConstructorDeclaration) md;
                            bodyText = cd2.getBody().toString();
                        }
                    } catch (Exception ignore) {}
                    row.put("body_hash", sha1(bodyText.getBytes(StandardCharsets.UTF_8)));
                    g.methods.add(row);

                    // calls inside this method/ctor
                    List<MethodCallExpr> calls = md.findAll(MethodCallExpr.class);
                    for (MethodCallExpr ce : calls) {
                        try {
                            ResolvedMethodDeclaration rmd = ce.resolve();
                            String declType = rmd.declaringType().getQualifiedName();
                            if (!internalFqns.contains(declType)) continue;

                            // build callee signature from resolved
                            List<String> calleeParamTypes = new ArrayList<>();
                            for (int i = 0; i < rmd.getNumberOfParams(); i++) {
                                String t = rmd.getParam(i).getType().describe();
                                calleeParamTypes.add(normalizeTypeString(t));
                            }
                            String calleeSig = rmd.getName() + "(" + String.join(",", calleeParamTypes) + ")";
                            // capture call-site arguments (expressions + best-effort types)
                            List<String> argExprs = new ArrayList<>();
                            List<String> argTypes = new ArrayList<>();
                            for (int ai = 0; ai < ce.getArguments().size(); ai++) {
                                try {
                                    var ex = ce.getArgument(ai);
                                    argExprs.add(ex.toString());
                                    try {
                                        ResolvedType at = ex.calculateResolvedType();
                                        argTypes.add(normalizeTypeString(at.describe()));
                                    } catch (Throwable t2) {
                                        argTypes.add("");
                                    }
                                } catch (Throwable t3) {
                                    argExprs.add("");
                                    argTypes.add("");
                                }
                            }

                            Map<String, Object> edge = new LinkedHashMap<>();
                            edge.put("project_name", projectName);
                            edge.put("repo_id", repoId);
                            edge.put("from_owner_fqn", ownerFqn);
                            edge.put("from_signature", signature);
                            edge.put("to_owner_fqn", declType);
                            edge.put("to_signature", calleeSig);
                            edge.put("file", rel);
                            edge.put("arg_exprs", argExprs);
                            edge.put("arg_types", argTypes);
                            g.calls.add(edge);

                            // also dependency
                            if (!declType.equals(ownerFqn)) {
                                g.dependencies.add(depEdge(projectName, repoId, ownerFqn, declType, "call", rel));
                            }
                        } catch (Throwable ignore) {
                            // resolution may fail for some calls; ignore
                        }
                    }
                }
            }
        }

        // Deduplicate edges
        g.dependencies = dedupeEdges(g.dependencies, List.of("from_fqn","to_fqn","via","file"));
        g.calls = dedupeEdges(g.calls, List.of("from_owner_fqn","from_signature","to_owner_fqn","to_signature","file"));
        g.extends_rel = dedupeEdges(g.extends_rel, List.of("child_fqn","parent_fqn"));
        g.implements_rel = dedupeEdges(g.implements_rel, List.of("child_fqn","iface_fqn"));

        ObjectMapper om = new ObjectMapper().enable(SerializationFeature.INDENT_OUTPUT);
        String json = om.writeValueAsString(toOutputShape(g));

        if (out != null && !out.isBlank()) {
            Files.writeString(Paths.get(out), json, StandardCharsets.UTF_8);
        } else {
            System.out.println(json);
        }
    }

    private static Map<String,Object> toOutputShape(Graph g) {
        Map<String,Object> out = new LinkedHashMap<>();
        out.put("project_name", g.project_name);
        out.put("repo_id", g.repo_id);
        out.put("types", g.types);
        out.put("methods", g.methods);
        out.put("fields", g.fields);
        out.put("dependencies", g.dependencies);

        // keep compatibility with existing GraphBuilder by using keys "extends" and "implements"
        List<Map<String,Object>> ext = g.extends_rel.stream().map(m -> Map.<String,Object>of(
                "project_name", m.get("project_name"),
                "repo_id", m.get("repo_id"),
                "child_fqn", m.get("child_fqn"),
                "parent_ref", m.get("parent_fqn")
        )).collect(Collectors.toList());
        List<Map<String,Object>> impl = g.implements_rel.stream().map(m -> Map.<String,Object>of(
                "project_name", m.get("project_name"),
                "repo_id", m.get("repo_id"),
                "child_fqn", m.get("child_fqn"),
                "iface_ref", m.get("iface_fqn")
        )).collect(Collectors.toList());
        out.put("extends", ext);
        out.put("implements", impl);
        out.put("calls", g.calls);
        return out;
    }

    private static Map<String, Object> relPair(String p, String r, String child, String parent) {
        Map<String,Object> m = new LinkedHashMap<>();
        m.put("project_name", p);
        m.put("repo_id", r);
        m.put("child_fqn", child);
        // store resolved fqn in parent_fqn/iface_fqn then mapped to parent_ref/iface_ref
        m.put("parent_fqn", parent);
        m.put("iface_fqn", parent);
        return m;
    }

    private static Map<String, Object> depEdge(String p, String r, String from, String to, String via, String file) {
        Map<String,Object> m = new LinkedHashMap<>();
        m.put("project_name", p);
        m.put("repo_id", r);
        m.put("from_fqn", from);
        m.put("to_fqn", to);
        m.put("to_simple", simpleName(to));
        m.put("via", via);
        m.put("file", file);
        return m;
    }

    private static String require(Map<String,String> a, String k) {
        if (!a.containsKey(k) || a.get(k)==null || a.get(k).isBlank()) {
            System.err.println("Missing required arg: --" + k);
            System.exit(2);
        }
        return a.get(k);
    }

    private static Map<String,String> parseArgs(String[] args) {
        Map<String,String> out = new HashMap<>();
        for (int i=0;i<args.length;i++) {
            String s = args[i];
            if (s.startsWith("--")) {
                String key = s.substring(2);
                String val = "true";
                if (i+1 < args.length && !args[i+1].startsWith("--")) {
                    val = args[i+1];
                    i++;
                }
                out.put(key, val);
            }
        }
        return out;
    }

    private static List<Path> findJavaFiles(Path root) throws IOException {
        try (var stream = Files.walk(root)) {
            return stream
                    .filter(p -> Files.isRegularFile(p) && p.toString().toLowerCase().endsWith(".java"))
                    .collect(Collectors.toList());
        }
    }

    private static List<Path> detectSourceRoots(Path root) throws IOException {
        List<Path> candidates = new ArrayList<>();
        Path m1 = root.resolve("src/main/java");
        Path m2 = root.resolve("src/test/java");
        if (Files.isDirectory(m1)) candidates.add(m1);
        if (Files.isDirectory(m2)) candidates.add(m2);

        // multi-module: search for any */src/main/java
        try (var stream = Files.walk(root, 4)) {
            stream.filter(p -> p.endsWith("src/main/java") && Files.isDirectory(p))
                  .forEach(candidates::add);
            stream.filter(p -> p.endsWith("src/test/java") && Files.isDirectory(p))
                  .forEach(candidates::add);
        } catch (Exception ignore) {}
        return candidates.stream().distinct().collect(Collectors.toList());
    }

    private static byte[] readBytesSafe(Path p) {
        try { return Files.readAllBytes(p); } catch (Exception e) { return new byte[0]; }
    }

    private static String sha1(byte[] b) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-1");
            byte[] d = md.digest(b);
            StringBuilder sb = new StringBuilder();
            for (byte x: d) sb.append(String.format("%02x", x));
            return sb.toString();
        } catch (Exception e) { return ""; }
    }

    private static String getFqn(CompilationUnit cu, TypeDeclaration<?> td) {
        try {
            Optional<String> fq = td.getFullyQualifiedName();
            if (fq.isPresent()) return fq.get();
        } catch (Exception ignore) {}
        // fallback
        String pkg = cu.getPackageDeclaration().map(pd -> pd.getNameAsString()).orElse("");
        String name = td.getNameAsString();
        if (pkg.isBlank()) return name;
        return pkg + "." + name;
    }

    private static String resolveTypeFqn(ClassOrInterfaceType t, Set<String> internal) {
        // best-effort: resolve and check internal
        try {
            // ClassOrInterfaceType#resolve() already returns a resolved type in modern JavaParser versions.
            ResolvedType rt = t.resolve();
            String q = rt.describe();
            String norm = normalizeTypeString(q);
            String internalHit = extractInternalFromTypeString(norm, internal);
            if (internalHit != null) return internalHit;
        } catch (Throwable ignore) {}
        // fallback on simple name matching
        String simple = t.getNameAsString();
        for (String fqn : internal) {
            if (fqn.endsWith("." + simple) || fqn.endsWith("$" + simple) || fqn.equals(simple)) return fqn;
        }
        return null;
    }

    private static String safeDescribeType(com.github.javaparser.ast.type.Type t, Set<String> internal) {
        try {
            ResolvedType rt = t.resolve();
            return normalizeTypeString(rt.describe());
        } catch (Throwable ex) {
            // fallback: raw
            return normalizeTypeString(t.asString());
        }
    }

    private static String normalizeTypeString(String s) {
        if (s == null) return "";
        // remove generics like java.util.List<com.x.User> -> com.x.User OR java.util.List
        String x = s.trim();
        // keep inner type if it's a simple generic container (List<User> -> User)
        if (x.contains("<") && x.contains(">")) {
            String inside = x.substring(x.indexOf('<')+1, x.lastIndexOf('>')).trim();
            if (!inside.isBlank() && !inside.contains(",")) {
                x = inside;
            } else {
                x = x.substring(0, x.indexOf('<'));
            }
        }
        // array normalization
        x = x.replace("[]", "[]");
        return x;
    }

    private static String extractInternalFromTypeString(String typeStr, Set<String> internal) {
        if (typeStr == null) return null;
        String base = typeStr.trim();
        // strip array
        base = base.replace("[]", "");
        // if fully qualified
        if (internal.contains(base)) return base;
        // if simple, match ending
        String simple = simpleName(base);
        for (String fqn : internal) {
            if (fqn.endsWith("." + simple) || fqn.endsWith("$" + simple) || fqn.equals(simple)) return fqn;
        }
        return null;
    }

    private static String simpleName(String fqnOrType) {
        if (fqnOrType == null) return "";
        String s = fqnOrType.replace("[]","");
        int i = Math.max(s.lastIndexOf('.'), s.lastIndexOf('$'));
        return i>=0 ? s.substring(i+1) : s;
    }

    private static List<Map<String,Object>> dedupeEdges(List<Map<String,Object>> edges, List<String> keys) {
        LinkedHashMap<String,Map<String,Object>> out = new LinkedHashMap<>();
        for (Map<String,Object> e : edges) {
            StringBuilder sb = new StringBuilder();
            for (String k: keys) sb.append(String.valueOf(e.get(k))).append("|");
            out.putIfAbsent(sb.toString(), e);
        }
        return new ArrayList<>(out.values());
    }

    private static class TypeMeta {
        public final String fqn;
        public final String name;
        public final String file;
        public TypeMeta(String fqn, String name, String file) {
            this.fqn = fqn; this.name = name; this.file = file;
        }
    }
}
