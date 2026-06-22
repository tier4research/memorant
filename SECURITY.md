# Security policy

Memorant is alpha software. The current supported line is `0.1.x`.

Do not open a public issue for a vulnerability or include private memory data in a
report. Use GitHub's private vulnerability reporting feature on this repository.
Include affected versions, reproduction steps, impact, and a suggested mitigation
when available. Maintainers will acknowledge a complete report as soon as practical
and coordinate disclosure after a fix is available.

Memorant stores potentially sensitive agent memory in a local SQLite file. Operators
are responsible for filesystem permissions, backups, host security, and controlling
which processes can read the database and standing-state files.
