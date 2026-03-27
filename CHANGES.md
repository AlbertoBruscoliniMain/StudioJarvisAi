# Modifiche apportate al progetto — StudioJarvisAi

Questo documento raccoglie tutte le modifiche fatte al codebase originale di [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) durante la sessione di sviluppo.

---

## Indice

1. [Dipendenze e setup iniziale](#1-dipendenze-e-setup-iniziale)
2. [Correzione configurazione modello](#2-correzione-configurazione-modello)
3. [Compilazione modulo Rust](#3-compilazione-modulo-rust)
4. [Nuova configurazione agente con tool](#4-nuova-configurazione-agente-con-tool)
5. [Nuovo tool: image_read](#5-nuovo-tool-image_read)
6. [Registrazione del tool in __init__.py](#6-registrazione-del-tool-in-__init__py)
7. [Endpoint /v1/upload nel server](#7-endpoint-v1upload-nel-server)
8. [Filtro modelli vision-only nel selettore](#8-filtro-modelli-vision-only-nel-selettore)
9. [Fix routing streaming → agente](#9-fix-routing-streaming--agente)
10. [UI: upload immagini e incolla screenshot](#10-ui-upload-immagini-e-incolla-screenshot)
11. [Conoscenza da W3Schools](#11-conoscenza-da-w3schools)
12. [Come avviare il progetto](#12-come-avviare-il-progetto)

---

## 1. Dipendenze e setup iniziale

Il progetto richiede **Python 3.12**, **Node.js**, **Ollama** e **Rust** (per il modulo SQLite). L'ambiente Python è gestito con `uv`.

```bash
# Installa uv (package manager Python)
brew install uv

# Installa le dipendenze Python
cd OpenJarvis
uv sync

# Installa dipendenze frontend
cd frontend && npm install
```

Modelli Ollama necessari:

```bash
ollama pull qwen3:8b        # modello chat principale
ollama pull moondream:latest # modello vision per analisi immagini
```

---

## 2. Correzione configurazione modello

**File:** `~/.openjarvis/config.toml`

Il modello originale configurato (`qwen3.5:14b`) non era installato localmente, causando errori 404 su ogni richiesta all'engine.

**Prima:**
```toml
[intelligence]
default_model = "qwen3.5:14b"

[agent]
default_agent = "simple"
```

**Dopo:**
```toml
[intelligence]
default_model = "qwen3:8b"

[agent]
default_agent = "orchestrator"
tools = "image_read,web_search,code_interpreter,file_read,memory_search,memory_retrieve,memory_manage,http_request,shell_exec,think"
```

Il cambio da `simple` a `orchestrator` è fondamentale: l'agente `simple` non chiama mai i tool, mentre l'`orchestrator` implementa un loop di tool-calling multi-turno.

---

## 3. Compilazione modulo Rust

Il backend usa un modulo Rust (`openjarvis_rust`) per il backend SQLite della memoria. Va compilato manualmente con `maturin` ogni volta che si esegue `uv sync` (che lo rimuove).

```bash
source .venv/bin/activate

maturin develop --manifest-path rust/crates/openjarvis-python/Cargo.toml
```

Output atteso:
```
📦 Built wheel for CPython 3.12 ...
✏️  Setting installed package as editable
🛠  Installed openjarvis-rust-0.1.0
```

> **Nota:** senza questo step, `jarvis memory index` e il backend di memoria falliscono con `ModuleNotFoundError: openjarvis_rust`.

---

## 4. Nuova configurazione agente con tool

**File:** `~/.openjarvis/config.toml`

Per far sì che il server web usi l'orchestrator con i tool, bisogna dichiarare la lista `tools` nella sezione `[agent]` (stringa separata da virgole) **e** nella sezione `[tools]` (lista TOML):

```toml
[agent]
default_agent = "orchestrator"
tools = "image_read,web_search,code_interpreter,file_read,memory_search,memory_retrieve,memory_manage,http_request,shell_exec,think"

[tools]
enabled = ["code_interpreter", "web_search", "file_read", "shell_exec", "browser_navigate", "browser_get_text", "http_request", "memory_manage", "retrieval", "image_read"]
```

Le due chiavi servono a sistemi diversi: `[agent].tools` è letta dal server API, `[tools].enabled` è letta dalla CLI.

---

## 5. Nuovo tool: image_read

**File:** `src/openjarvis/tools/image_read.py` *(nuovo file)*

Tool personalizzato che analizza immagini usando il modello vision `moondream` tramite Ollama. Converte l'immagine in base64 e la invia all'endpoint `/api/chat` di Ollama.

```python
@ToolRegistry.register("image_read")
class ImageReadTool(BaseTool):
    """Analyze or describe an image using a local vision model (moondream)."""

    tool_id = "image_read"

    def execute(self, **params: Any) -> ToolResult:
        path = params.get("path", "").strip()
        question = params.get("question", "Describe this image in detail.")
        model = params.get("model", "moondream:latest")

        img_path = Path(path).expanduser()
        b64 = base64.b64encode(img_path.read_bytes()).decode("utf-8")

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": question, "images": [b64]}],
            "stream": False,
        }

        with httpx.Client(timeout=120.0) as client:
            resp = client.post("http://localhost:11434/api/chat", json=payload)
            resp.raise_for_status()

        description = resp.json().get("message", {}).get("content", "").strip()
        return ToolResult(tool_name="image_read", content=description, success=True)
```

**Formati supportati:** `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`

Il tool accetta tre parametri:
- `path` *(obbligatorio)* — percorso assoluto all'immagine
- `question` *(opzionale)* — domanda specifica sull'immagine
- `model` *(opzionale)* — modello vision da usare (default: `moondream:latest`)

---

## 6. Registrazione del tool in `__init__.py`

**File:** `src/openjarvis/tools/__init__.py`

Il sistema di tool usa il pattern del decorator `@ToolRegistry.register()` che si attiva all'import. Bisogna aggiungere l'import del nuovo modulo per far sì che il tool venga registrato all'avvio:

```python
# Aggiunto in fondo alla lista degli import
try:
    import openjarvis.tools.image_read  # noqa: F401
except ImportError:
    pass
```

Senza questo, l'orchestrator non trova `image_read` nel registry e non lo include nei tool disponibili.

---

## 7. Endpoint `/v1/upload` nel server

**File:** `src/openjarvis/server/routes.py`

Aggiunto endpoint POST per ricevere immagini dal frontend, salvarle in `~/.openjarvis/uploads/` e restituire il percorso locale. Il percorso viene poi passato al tool `image_read`.

```python
_UPLOAD_DIR = pathlib.Path.home() / ".openjarvis" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

@router.post("/v1/upload")
async def upload_image(file: UploadFile = File(...)):
    """Salva un'immagine caricata e restituisce il percorso locale."""
    suffix = pathlib.Path(file.filename or "image.png").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")
    dest = _UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"path": str(dest), "filename": file.filename}
```

**Risposta esempio:**
```json
{
  "path": "/Users/alby/.openjarvis/uploads/a3f2c1d4e5b6.png",
  "filename": "screenshot.png"
}
```

---

## 8. Filtro modelli vision-only nel selettore

**File:** `src/openjarvis/server/routes.py`

`moondream` è un modello vision-only: non può rispondere a messaggi di testo e produce output privi di senso se selezionato come modello chat. Viene escluso dalla lista restituita dall'endpoint `/v1/models`.

```python
@router.get("/v1/models")
async def list_models(request: Request) -> ModelListResponse:
    engine = request.app.state.engine
    # Modelli vision-only — usati internamente dai tool, non nel selettore chat
    _VISION_ONLY = {"moondream", "moondream:latest", "llava", "llava:latest"}
    model_ids = [m for m in engine.list_models() if m not in _VISION_ONLY]
    return ModelListResponse(data=[ModelObject(id=mid) for mid in model_ids])
```

---

## 9. Fix routing streaming → agente

**File:** `src/openjarvis/server/routes.py`

**Problema:** la condizione originale inviava le richieste streaming attraverso l'agente (con tool) **solo** se il client includeva `tools` nel corpo della richiesta. Il frontend non li inviava, quindi ogni richiesta bypassava l'orchestrator e andava direttamente all'engine LLM, che non ha capacità di tool calling.

**Prima:**
```python
if agent is not None and bus is not None and request_body.tools:
    return await _handle_agent_stream(agent, bus, model, request_body)
return await _handle_stream(engine, model, request_body, complexity_info)
```

**Dopo:**
```python
# Usa l'agente anche quando i tool sono configurati nel config (non solo nel request body)
agent_has_tools = agent is not None and getattr(agent, "_tools", None)
if agent is not None and bus is not None and (request_body.tools or agent_has_tools):
    return await _handle_agent_stream(agent, bus, model, request_body)
return await _handle_stream(engine, model, request_body, complexity_info)
```

`agent._tools` è la lista di tool istanziati dall'orchestrator al momento dell'avvio, caricata dal `config.toml`.

---

## 10. UI: upload immagini e incolla screenshot

**File:** `frontend/src/components/Chat/InputArea.tsx`

### Funzionalità aggiunte

- **Pulsante ImagePlus** nella toolbar per aprire il file picker (multiplo, fino a 10 immagini)
- **Cmd+V** per incollare screenshot direttamente dalla clipboard
- **Anteprima** delle immagini caricate con pulsante ✕ per rimuoverle singolarmente
- **Contatore** `N/10` accanto alle anteprime
- **Upload automatico** al momento della selezione, con salvataggio su `/v1/upload`

### Upload da file picker

```tsx
const uploadFile = useCallback(async (file: File, name: string) => {
  const formData = new FormData();
  formData.append('file', file, name);
  const res = await fetch('http://localhost:8000/v1/upload', { method: 'POST', body: formData });
  const data = await res.json();
  setUploadedImages((prev) => [...prev, { path: data.path, name, preview }]);
}, [uploadedImages.length]);
```

### Incolla da clipboard (Cmd+V)

```tsx
const handlePaste = useCallback(async (e: React.ClipboardEvent) => {
  const items = Array.from(e.clipboardData.items);
  const imageItem = items.find((item) => item.type.startsWith('image/'));
  if (!imageItem) return;
  e.preventDefault();
  const file = imageItem.getAsFile();
  const formData = new FormData();
  formData.append('file', file, `screenshot_${Date.now()}.png`);
  const res = await fetch('http://localhost:8000/v1/upload', { method: 'POST', body: formData });
  const data = await res.json();
  setUploadedImages((prev) => [...prev, { path: data.path, name: 'screenshot.png', preview }]);
}, []);
```

### Costruzione del messaggio con le immagini

Quando l'utente invia, il frontend costruisce un messaggio testuale che istruisce l'orchestrator a usare il tool `image_read` per ogni immagine:

```tsx
const imageParts = uploadedImages.map((img) => `"${img.path}"`).join(', ');
const content = uploadedImages.length > 0
  ? `Usa il tool image_read per analizzare ${
      uploadedImages.length > 1
        ? `queste immagini una alla volta: ${imageParts}`
        : `l'immagine in ${imageParts}`
    }${text ? `. ${text}` : ' e descrivi cosa vedi.'}`
  : text;
```

---

## 11. Conoscenza da W3Schools

Per insegnare a Jarvis contenuti da un sito web, si usa il sistema di memoria con indicizzazione FTS5.

### Script di scraping

```python
# ~/.openjarvis/knowledge/fetch_w3schools.py
import subprocess, re
from html.parser import HTMLParser
from pathlib import Path

# Fetch con curl per evitare problemi SSL di Python
result = subprocess.run(
    ['curl', '-s', '-L', '--max-time', '10', url],
    capture_output=True, text=True
)
html = result.stdout

# Pulizia HTML: rimozione script/style, decodifica entities
# Estrazione contenuto utile partendo da sezioni chiave
```

### Indicizzazione in memoria

```bash
# Dopo aver scaricato le pagine
jarvis memory index ~/.openjarvis/knowledge/w3schools/
```

La conoscenza viene salvata nel database SQLite con full-text search. L'orchestrator la recupera automaticamente come contesto nelle risposte.

---

## 12. Come avviare il progetto

```bash
cd "/Users/alby/Desktop/PROGETTO AI PERSONALE/OpenJarvis"

# Attiva l'ambiente Python
source .venv/bin/activate

# (Solo dopo uv sync) Ricompila il modulo Rust
maturin develop --manifest-path rust/crates/openjarvis-python/Cargo.toml

# Avvia il backend
jarvis serve --host 0.0.0.0 --port 8000

# In un secondo terminale: avvia il frontend
cd frontend && npm run dev
```

Apri il browser su **http://localhost:5173**

### Fermare tutto

```bash
lsof -ti :8000 | xargs kill -9   # ferma backend
lsof -ti :5173 | xargs kill -9   # ferma frontend
```

---

*Documento generato il 27/03/2026 — Alberto Bruscolini × Claude Sonnet 4.6*
