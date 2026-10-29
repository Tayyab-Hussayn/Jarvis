---
paths:
  - "android/**"
---

# Android (Kotlin) conventions

- Toolchain matches `~/code/smart-sticky-wallpaper` (Gradle 9.8.1, AGP 9.x, Kotlin 2.4.x, JDK 21, `~/Android/Sdk`). AGP 9 changed the DSL: no `kotlinOptions {}` — use `kotlin { compilerOptions { … } }` / `jvmToolchain`; check AGP 9 release notes via context7 before guessing.
- Build output, `.gradle/`, `.kotlin/` and `local.properties` are never committed.
- Logic in plain Kotlin classes with JVM unit tests; Android framework code stays thin (Activity/Service/Accessibility glue).
- Network input is untrusted: parse into typed models, reject unknown commands, bound sizes and timeouts.
- Secrets (pairing token) only in encrypted storage; never in logs, never in `BuildConfig`.
