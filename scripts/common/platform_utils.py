#!/usr/bin/env python3
"""
Utilitários multiplataforma (Linux / Windows) para a esteira DeyeDoc.
Garante compatibilidade total entre sistemas operacionais:
- Resolução segura de binários (pdfcraft-cli, pdftotext)
- Resolução de templates sem caminhos hardcoded de usuário
- Diretórios temporários de alta performance (/dev/shm no Linux, %TEMP% no Windows)
- Extração de texto portátil (fallback nativo para pdfcraft-cli text quando pdftotext não estiver instalado)
- Tratamento estrito de codificação UTF-8
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess
from pathlib import Path

# Configura encoding UTF-8 seguro para stdout/stderr no Windows (evita UnicodeEncodeError com emojis)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def is_windows() -> bool:
    """Verifica se está rodando em ambiente Windows."""
    return sys.platform.startswith("win") or os.name == "nt"


def get_fast_temp_dir() -> Path:
    """
    Retorna diretório temporário mais rápido disponível:
    - /dev/shm no Linux (RAM disk, I/O instantâneo em memória)
    - %TEMP% / tempfile.gettempdir() no Windows e outros sistemas
    """
    if not is_windows() and os.path.exists("/dev/shm") and os.access("/dev/shm", os.W_OK):
        return Path("/dev/shm")
    return Path(tempfile.gettempdir())


def get_temp_file(prefix: str = "deye_", suffix: str = ".pdf") -> Path:
    """Gera um caminho de arquivo temporário seguro e único."""
    temp_dir = get_fast_temp_dir()
    import uuid
    uid = uuid.uuid4().hex[:8]
    return temp_dir / f"{prefix}{os.getpid()}_{uid}{suffix}"


def get_pdfcraft_cmd() -> str:
    """
    Localiza o binário ou script executável do pdfcraft-cli de forma compatível
    tanto no Linux quanto no Windows (procurando .exe, .cmd, .bat e PATH do npm/Python).
    """
    candidates = ["pdfcraft-cli", "pdfcraft-cli.exe", "pdfcraft-cli.cmd", "pdfcraft-cli.bat"]
    for c in candidates:
        found = shutil.which(c)
        if found:
            return found

    # Verificações adicionais específicas para Windows
    if is_windows():
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            npm_cmd = Path(appdata) / "npm" / "pdfcraft-cli.cmd"
            if npm_cmd.exists():
                return str(npm_cmd)

        py_scripts = Path(sys.executable).parent / "Scripts"
        for name in ["pdfcraft-cli.exe", "pdfcraft-cli.cmd", "pdfcraft-cli"]:
            p = py_scripts / name
            if p.exists():
                return str(p)

    return "pdfcraft-cli"


def run_pdfcraft(subcommand_args, capture_output=True, text=True, check=False):
    """
    Executa comandos do pdfcraft-cli de maneira robusta e multiplataforma.
    Usa shell=True quando necessário no Windows (ex.: scripts .cmd/.bat do npm).
    """
    bin_path = get_pdfcraft_cmd()
    cmd = [bin_path] + [str(a) for a in subcommand_args]

    use_shell = False
    if is_windows():
        bin_lower = bin_path.lower()
        if bin_lower.endswith((".cmd", ".bat")):
            use_shell = True

    try:
        res = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=text,
            encoding="utf-8",
            errors="replace",
            shell=use_shell,
            check=check
        )
        return res
    except FileNotFoundError:
        # Se falhou sem shell no Windows, tenta novamente com shell=True
        if is_windows() and not use_shell:
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=capture_output,
                    text=text,
                    encoding="utf-8",
                    errors="replace",
                    shell=True,
                    check=check
                )
                return res
            except Exception as e:
                print(f"[ERRO CRÍTICO] Falha ao executar pdfcraft-cli: {e}", file=sys.stderr)
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))
        else:
            print(f"[ERRO CRÍTICO] Executável do pdfcraft-cli não encontrado: {bin_path}", file=sys.stderr)
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="pdfcraft-cli not found")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))


def extract_text_from_pdf(pdf_path: str, layout: bool = False) -> str:
    """
    Extrai texto de um PDF de forma 100% portável.
    - Se pdftotext estiver disponível no sistema, utiliza-o.
    - Caso contrário, utiliza 'pdfcraft-cli text <pdf>' como fallback nativo universal.
    """
    p_path = Path(pdf_path).resolve()
    pdftotext_bin = shutil.which("pdftotext") or shutil.which("pdftotext.exe")

    if pdftotext_bin:
        cmd = [pdftotext_bin]
        if layout:
            cmd.append("-layout")
        cmd.extend([str(p_path), "-"])
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            if r.returncode == 0 and r.stdout:
                return r.stdout
        except Exception:
            pass

    # Fallback multiplataforma garantido pelo pdfcraft-cli
    res = run_pdfcraft(["text", str(p_path)])
    if res and res.returncode == 0 and res.stdout:
        return res.stdout

    return ""


def resolve_template_path(template_filename: str) -> Path:
    """
    Resolve o caminho de um template (.pdf ou .docx) de forma agnóstica de SO e usuário:
    1. Verifica na pasta oficial 'resources/templates' dentro da skill.
    2. Fallback nas pastas Documentos / Documents do usuário atual.
    """
    skill_root = Path(__file__).resolve().parent.parent.parent
    builtin = skill_root / "resources" / "templates" / template_filename
    if builtin.exists():
        return builtin

    # Fallbacks no diretório do usuário
    user_home = Path.home()
    candidates = [
        user_home / "Documentos" / template_filename,
        user_home / "Documents" / template_filename,
        user_home / "Documentos" / "Datasheets OnGrid" / template_filename,
        user_home / "Documents" / "Datasheets OnGrid" / template_filename,
        user_home / "Documentos" / "datasheets ongrid" / template_filename,
        user_home / "Documents" / "datasheets ongrid" / template_filename,
    ]
    for c in candidates:
        if c.exists():
            return c

    return builtin
