---
name: deyedoc
description: Processa datasheets de inversores Deye em PDF, traduz para o padrão de engenharia brasileiro via pdfcraft e gera PET (.docx/.pdf) e DCONF (.pdf). Acione via /deyedoc.
argument-hint: "[--mode all|single|new] [--module all|datasheet|pet|dconf] [--auto|--manual]"
---

# Skill / Slash Command: `/deyedoc` (Deye Datasheet to Docs)

Esta skill processa datasheets de inversores Deye (em inglês ou português), adapta para a engenharia brasileira e automatiza a geração dos 3 documentos de homologação:
1. **Novo Datasheet em PDF** (editado diretamente pelo `pdfcraft-cli` sobre o PDF base via motor semântico adaptativo).
2. **PET - Planilha de Especificações Técnicas INMETRO** (arquivos `.docx` e `.pdf` por modelo a partir de `pet_modelo.docx` ou `pet_modelo_ongrid.docx`).
3. **DCONF - Declaração de Conformidade do Fornecedor** (a partir de `BASE_DCONF.pdf`).

---

## ⚠️ REGRA MANDATÓRIA DE COMPORTAMENTO DO ASSISTENTE

> [!IMPORTANT]
> **NUNCA EXECUTE NENHUM SCRIPT OU GERAÇÃO AUTOMÁTICA SEM PERMISSÃO EXPLÍCITA E TODAS AS INFORMAÇÕES NECESSÁRIAS!**
> 
> Se o usuário acionar `/deyedoc`, enviar uma mensagem ou anexar um datasheet **sem especificar comandos/parâmetros completos na mensagem**:
> 1. O assistente **NUNCA roda o pipeline ou cria arquivos automaticamente**.
> 2. O assistente deve **apenas analisar o PDF base em background** (identificando idioma nativo, modelos presentes e topologia).
> 3. Em seguida, o assistente **DEVE SEMPRE parar e questionar o usuário obrigatoriamente** antes de prosseguir com qualquer execução.
> 4. Quando o objetivo for **Derivar / Criar novos modelos**, as seguintes 4 perguntas de engenharia **DEVEM ser feitas de forma OBRIGATÓRIA**, pois a skill não pode executar sem ter todas as informações necessárias:
>    1. **Potência nominal (kW):** Potência(s) em kW desejada(s) (ex: `7.5`, `8`, `10`, `14`).
>    2. **Tensão de rede para o Brasil:** `220/380V` (Padrão Trifásico 380V) ou `127/220V` (Trifásico Baixa Tensão `-LV`), ou monofásico `220V`/`127V`.
>    3. **Modelo de referência:** Qual dos modelos originais do datasheet servirá de base técnica para herdar dimensões e sobrecarga CC/CA.
>    4. **Nomenclatura / Part Number:** Sugestão automática oficial (ex: `SUN-7.5K-SG06LP3-EU-BM2`), permitindo confirmação ou customização.
> 5. Somente após o usuário responder e validar todas as informações necessárias, o assistente poderá executar os scripts de geração.

---

## 1. Fluxo de Triagem e Modo Rápido Inicial

Ao receber o datasheet, informe a análise inicial (modelos identificados, topologia e idioma) e ofereça a escolha de velocidade antes de qualquer pergunta detalhada:

### Pergunta Inicial: Velocidade de Execução
- **[1] Modo Rápido Ultra-Fast (~2.0s) [Padrão]:** Gera imediatamente o pacote completo (**Datasheet PDF**, **DCONF PDF**, e todas as **Planilhas PET (.docx e .pdf)**) em cerca de **2 segundos** via motor vetorial nativo `pdfcraft-cli`, 100% livre de LibreOffice.
- **[2] Modo Personalizado Guiado:** Permite customizar modelos, potências, tensões específicas e selecionar entregáveis.

Se o usuário escolher o **Modo Personalizado Guiado**, prossiga com as perguntas detalhadas:

### Pergunta 1: Qual é o objetivo principal?
- **[1] Traduzir / Adequar todos os modelos:** Mantém os modelos originais do documento e adequa para o padrão brasileiro (ou faz checkup de termos e contatos se já for PT-BR).
- **[2] Traduzir / Focar em modelo único:** Filtra e gera os documentos exclusivamente para um único modelo selecionado.
- **[3] Derivar / Criar novos modelos:** Cria novas variantes (potências customizadas, tensões específicas) a partir de um modelo de referência do documento (seja o base em inglês ou português).

### Pergunta 2: Quais documentos deseja gerar?
- **[1] Pacote Completo:** Datasheet (PDF) + PET (DOCX + PDF) + DCONF (PDF) *(Padrão)*
- **[2] Apenas Datasheet (PDF)**
- **[3] Apenas Planilhas Técnicas PET INMETRO (DOCX + PDF)**
- **[4] Apenas Declaração de Conformidade DCONF (PDF)**

### Pergunta 3: Configuração de Novos Modelos (OBRIGATÓRIO para opção 3)
1. **Potência nominal (kW):** Qual a potência nominal em kW do novo modelo? (ex: `7.5`, `8`, `10`, `14`, `15`)
2. **Tensão de rede para o Brasil:**
   - Se a topologia for **Trifásica**: `220/380V` (Padrão 380V) ou `127/220V` (Trifásico Baixa Tensão, sufixo `-LV`).
   - Se a topologia for **Monofásica / EU**: `220V` (Padrão) ou `127V`.
3. **Modelo de referência:** Qual modelo base do documento deve fornecer as razões de sobredimensionamento e dimensões mecânicas?
4. **Nomenclatura / Part Number:** Apresentar a sugestão automática calculada (ex: `SUN-7.5K-SG06LP3-EU-BM2`) para confirmação ou alteração.
5. Deseja adicionar mais algum modelo?

### Pergunta 4: Modo de Execução
- **[1] Modo Automático / Direto:** Gera imediatamente com os padrões oficiais.
- **[2] Modo Manual de Engenharia:** Apresenta a **Tabela Resumo de Engenharia** (Modelo, Potência, Tensão CA, Fases, $I_{\text{nom}}$, $I_{\text{max}}$, Tempo EPS, Garantia) para conferência antes de iniciar a gravação.

---

## 2. Dados e Valores Fixos Padronizados (Deye Brasil)

Os seguintes valores corporativos e regulatórios são **estritamente padronizados em todos os documentos gerados**:

1. **Dados de Contato e Suporte Oficial:**
   - **E-mail:** `suporte@deyeinversores.com.br`
   - **Telefones de Atendimento:**
     - Inversores Híbridos (linha SG): `(11) 2500-3700`
     - Demais Inversores (On-Grid linha G e Microinversores): `(11) 2500-0681`
   - **Endereço Oficial:** `Av. José Meloni, 295 - Vila Mogilar, Mogi das Cruzes - SP`
   - **Website:** `www.deyeinversores.com.br`
   - **Razões Sociais:**
     - Fabricante: *Ningbo Deye Inverter Technology Co., Ltd.*
     - Solicitante / Representante no Brasil: *DEYE BRASIL SUPPORT CENTER COMERCIO DE INVERSORES FOTOVOLTAICOS LTDA* (CNPJ: `32.574.888/0001-62`).

2. **Garantia de Fábrica:**
   - **10 Anos** para **todos** os equipamentos (Inversores Híbridos, Inversores On-Grid e Microinversores).
   - Nota de rodapé oficial: `*Período de garantia de 10 anos conforme a política de garantia da Deye Brasil.`

3. **Parâmetros Elétricos e Normas Técnicas:**
   - **Frequência da Rede:** `60 Hz` (faixa operacional de `57,5 ~ 62 Hz`).
   - **Fator de Potência:** `0,8 adiantado a 0,8 atrasado`.
   - **Portaria INMETRO:** Portaria **140/2022**.
   - **Validade da DCONF:** Exatamente **1 ano** a partir da data de emissão (`DD/MM/AAAA+1`).
   - **Laboratório e Relatório de Ensaio:** Mantidos com valores padrão regulatórios.

---

## 3. Regras de Engenharia Elétrica

1. **Relação de Potência CC/CA e Sobredimensionamento:**
   - Obtidas do modelo de referência no datasheet base:
     $$\text{Ratio}_{\text{CC}} = \frac{P_{\text{máx CC}}}{P_{\text{nominal CA}}} \quad\quad \text{Ratio}_{\text{Acesso}} = \frac{P_{\text{acesso FV}}}{P_{\text{nominal CA}}}$$
   - Ao criar novos modelos derivados, aplicar exatamente as mesmas razões para dimensionar a potência CC e de acesso.

2. **Correntes CA Unificadas para o Brasil:**
   - **Monofásicos 220V:** $I_{\text{nom}} = \frac{P_{\text{nom}}}{220\text{V}}$
   - **Monofásicos 127V:** $I_{\text{nom}} = \frac{P_{\text{nom}}}{127\text{V}}$
   - **Trifásicos 380V:** $I_{\text{nom}} = \frac{P_{\text{nom}}}{\sqrt{3} \times 380\text{V}}$
   - **Trifásicos 220V (-LV):** $I_{\text{nom}} = \frac{P_{\text{nom}}}{\sqrt{3} \times 220\text{V}}$
   - Corrente máxima calculada a partir da potência aparente máxima ($S_{\text{max}} = 1,10 \times P_{\text{nom}}$).

3. **Mapeamento de Tensões na PET INMETRO:**
   | Topologia e Tensão | Tensão Nominal (`tensao_nom`) | Outras Tensões (`outras_tensoes`) |
   | :--- | :--- | :--- |
   | **Monofásico 127V** | `127V` | `120V` |
   | **Monofásico / Bifásico 220V** | `220V` | `230V` |
   | **Trifásico 127/220V (`-LV`)** | `127/220V` | `120/208V` |
   | **Trifásico 220/380V (Padrão)** | `220/380V` | `230/400V` |

4. **Tempo de Transferência (Modo Backup / EPS):**
   - **Modelos P1, P2 e LP3:** `< 10 ms`
   - **Modelos HP3 (Alta Tensão Trifásico):** `< 20 ms`

5. **Regra de Corrente Máxima FV na PET (`fv_corrente_max`):**
   - Se o datasheet apresentar somatórios (ex: `36+36`, `26+40`), utiliza-se **estritamente o maior número individual** (ex: `36` ou `40`), sem sinais de soma.

6. **Diagramação e Prevenção de Quebra de Linha na PET (.docx):**
   - A linha de dados técnicos (Linha 11) aplica a propriedade OpenXML `<w:noWrap/>` dentro de `<w:tcPr>` e fonte calibrada em `5.5 pt` (`sz: 11`), eliminando quebras espúrias.
   - Orientação forçada em paisagem (`landscape`), sem páginas em branco extras.

---

## 4. Estrutura de Arquivos e Execução CLI

```
~/.agents/skills/deyedoc/
├── SKILL.md                          # Este documento mestre da skill /deyedoc
├── resources/
│   └── templates/                    # Modelos base oficiais
│       ├── pet_modelo.docx           # Template Word Híbrido (SG)
│       ├── pet_modelo_ongrid.docx    # Template Word On-Grid (G)
│       └── BASE_DCONF.pdf            # Declaração de Conformidade Oficial
└── scripts/
    ├── pipeline.py                   # Orquestrador mestre interativo e CLI
    ├── common/
    │   ├── deye_grammar.py           # Decodificador gramatical e dados corporativos fixos
    │   └── electrical_calc.py        # Cálculos de correntes e potências
    └── modules/
        ├── mod_datasheet.py          # Módulo 1: Datasheet PDF
        ├── mod_pet.py                # Módulo 2: PET INMETRO (.docx e .pdf)
        └── mod_dconf.py              # Módulo 3: DCONF (.pdf)
```

### Comandos CLI:

1. **Modo Rápido Ultra-Fast (~1.5s):**
   ```bash
   python3 ~/.agents/skills/deyedoc/scripts/pipeline.py /caminho/do/datasheet.pdf --fast
   ```

2. **Modo Interativo Guiado (Pergunta velocidade e configurações):**
   ```bash
   python3 ~/.agents/skills/deyedoc/scripts/pipeline.py /caminho/do/datasheet.pdf
   ```

3. **Comando Direto (Quando o usuário fornecer comandos explícitos):**
   ```bash
   # Todos os modelos:
   python3 ~/.agents/skills/deyedoc/scripts/pipeline.py /caminho/do/datasheet.pdf --mode all --auto

   # Modelo único específico:
   python3 ~/.agents/skills/deyedoc/scripts/pipeline.py /caminho/do/datasheet.pdf --mode single --single-model "SUN-10K-SG02LP2-US-AM2" --auto

   # Novos modelos derivados com potências específicas:
   python3 ~/.agents/skills/deyedoc/scripts/pipeline.py /caminho/do/datasheet.pdf --mode new --reference 1 --powers "14,16" --voltage 380 --auto

   # Gerar apenas um entregável (ex: apenas PET):
   python3 ~/.agents/skills/deyedoc/scripts/pipeline.py /caminho/do/datasheet.pdf --module pet --auto
   ```
