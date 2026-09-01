"""Migration de la configuration personnelle et des données vers `JOBSEARCH_HOME`.

Ne fait QUE des copies (`shutil.copy2`/copie fichier à fichier) — ne déplace ni
ne supprime jamais rien dans le dépôt d'origine. Mode « à blanc » par défaut :
il faut passer `apply=True` pour écrire quoi que ce soit sur le disque.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field

from src.config import PACKAGE_ROOT, PATHS


@dataclass
class MigrationItem:
    label: str
    kind: str  # "file" ou "dir"
    src: str
    dst: str


def build_plan() -> list[MigrationItem]:
    """Construit la liste des copies à effectuer (source dans le dépôt → destination `JOBSEARCH_HOME`)."""
    items = [
        MigrationItem("Config recherche (search.yaml)", "file",
                       os.path.join(PACKAGE_ROOT, "config", "search.yaml"),
                       os.path.join(PATHS.config_dir, "search.yaml")),
        MigrationItem("Profil candidat (profile.yaml)", "file",
                       os.path.join(PACKAGE_ROOT, "config", "profile.yaml"),
                       os.path.join(PATHS.config_dir, "profile.yaml")),
        MigrationItem("Sources personnalisées (sources.yaml)", "file",
                       os.path.join(PACKAGE_ROOT, "config", "sources.yaml"),
                       os.path.join(PATHS.config_dir, "sources.yaml")),
        MigrationItem("Données CV (cv_data.yaml)", "file",
                       os.path.join(PACKAGE_ROOT, "cv_master", "cv_data.yaml"),
                       os.path.join(PATHS.cv_dir, "cv_data.yaml")),
        MigrationItem("Résumé CV (cv_master.md)", "file",
                       os.path.join(PACKAGE_ROOT, "cv_master", "cv_master.md"),
                       os.path.join(PATHS.cv_dir, "cv_master.md")),
        MigrationItem("Secrets (.env)", "file",
                       os.path.join(PACKAGE_ROOT, ".env"),
                       PATHS.env_path),
        MigrationItem("Offres et suivi (data/)", "dir",
                       os.path.join(PACKAGE_ROOT, "data"),
                       PATHS.data_dir),
        MigrationItem("Candidatures générées (output/)", "dir",
                       os.path.join(PACKAGE_ROOT, "output"),
                       PATHS.output_dir),
    ]
    return items


def _iter_files(root: str) -> list[str]:
    """Chemins relatifs (POSIX) de tous les fichiers sous `root`, triés."""
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def _dir_stats(root: str) -> tuple[int, int]:
    """(nombre de fichiers, volume total en octets)."""
    count = 0
    size = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            count += 1
            size += os.path.getsize(os.path.join(dirpath, name))
    return count, size


def _dir_checksum(root: str) -> str:
    """Empreinte sha256 du contenu concaténé des fichiers, dans l'ordre trié des chemins relatifs."""
    h = hashlib.sha256()
    for rel in _iter_files(root):
        with open(os.path.join(root, rel), "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def _file_checksum(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _human_size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} To"


def dry_run(items: list[MigrationItem]) -> None:
    print("Migration — mode à blanc (rien n'est écrit sur le disque)\n")
    print(f"Destination : {os.path.dirname(PATHS.config_dir)}\n")
    any_source = False
    for item in items:
        if not os.path.exists(item.src):
            print(f"  [absent]  {item.label}\n            source introuvable : {item.src}")
            continue
        any_source = True
        if item.kind == "file":
            size = os.path.getsize(item.src)
            print(f"  [fichier] {item.label}")
            print(f"            {item.src}")
            print(f"         -> {item.dst}  ({_human_size(size)})")
        else:
            count, size = _dir_stats(item.src)
            print(f"  [dossier] {item.label}")
            print(f"            {item.src}")
            print(f"         -> {item.dst}  ({count} fichier(s), {_human_size(size)})")
    print()
    if not any_source:
        print("Aucune source trouvée dans le dépôt — rien à migrer.")
    else:
        print("Pour copier réellement : python main.py --migrate --apply")


def _copy_file(item: MigrationItem, conflicts: list[str], copied: list[str]) -> None:
    if os.path.exists(item.dst):
        conflicts.append(f"{item.label} : destination déjà présente, ignorée ({item.dst})")
        return
    os.makedirs(os.path.dirname(item.dst), exist_ok=True)
    shutil.copy2(item.src, item.dst)
    copied.append(item.label)


def _copy_dir(item: MigrationItem, conflicts: list[str], copied: list[str]) -> None:
    os.makedirs(item.dst, exist_ok=True)
    n = 0
    for rel in _iter_files(item.src):
        src_path = os.path.join(item.src, rel)
        dst_path = os.path.join(item.dst, rel)
        if os.path.exists(dst_path):
            conflicts.append(f"{item.label} : {rel} déjà présent à destination, ignoré")
            continue
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        shutil.copy2(src_path, dst_path)
        n += 1
    if n:
        copied.append(f"{item.label} ({n} fichier(s))")


def apply_migration(items: list[MigrationItem]) -> None:
    print("Migration — copie réelle\n")
    conflicts: list[str] = []
    copied: list[str] = []
    for item in items:
        if not os.path.exists(item.src):
            print(f"  [absent]  {item.label} — source introuvable, ignorée")
            continue
        if item.kind == "file":
            _copy_file(item, conflicts, copied)
        else:
            _copy_dir(item, conflicts, copied)

    print()
    if copied:
        print("Copié :")
        for c in copied:
            print(f"  - {c}")
    if conflicts:
        print("\nIgnoré (déjà présent à destination — jamais écrasé) :")
        for c in conflicts:
            print(f"  - {c}")

    print("\nVérification (fichiers + somme de contrôle) :")
    ok = True
    for item in items:
        if not os.path.exists(item.src):
            continue
        if item.kind == "file":
            if not os.path.exists(item.dst):
                print(f"  ✗ {item.label} : absent à destination")
                ok = False
                continue
            same = _file_checksum(item.src) == _file_checksum(item.dst)
            print(f"  {'✓' if same else '✗'} {item.label} : {'identique' if same else 'DIFFÉRENT'}")
            ok = ok and same
        else:
            src_count, _ = _dir_stats(item.src)
            if not os.path.exists(item.dst):
                print(f"  ✗ {item.label} : destination absente")
                ok = False
                continue
            dst_count, _ = _dir_stats(item.dst)
            src_sum = _dir_checksum(item.src)
            dst_sum = _dir_checksum(item.dst)
            same = src_count == dst_count and src_sum == dst_sum
            print(
                f"  {'✓' if same else '✗'} {item.label} : "
                f"{src_count} → {dst_count} fichier(s), empreintes "
                f"{'identiques' if src_sum == dst_sum else 'DIFFÉRENTES'}"
            )
            ok = ok and same

    print()
    if ok:
        print("Vérification OK : les copies sont identiques aux originaux.")
    else:
        print("⚠️  Incohérence détectée — vérifie les messages ci-dessus avant de continuer.")

    print(
        "\nLes fichiers d'origine restent INCHANGÉS dans le dépôt "
        f"({PACKAGE_ROOT}). Rien n'a été déplacé ni supprimé.\n"
        "Une fois que tu as vérifié que tout fonctionne depuis "
        f"{os.path.dirname(PATHS.config_dir)}, tu peux supprimer les originaux toi-même si tu le souhaites."
    )


def run_migrate(apply: bool = False) -> None:
    items = build_plan()
    if apply:
        apply_migration(items)
    else:
        dry_run(items)
