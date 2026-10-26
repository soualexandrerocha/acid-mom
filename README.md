# Acid Mom

Rastreador automático de acidentes no transporte marítimo de cargas, no mundo todo.

De hora em hora, a ferramenta:

1. **Coleta** notícias de sites do setor (RSS), notícias do mundo todo (GDELT) e avisos náuticos (NAVAREA, da NGA).
2. **Filtra** por palavras-chave, sem custo, o que claramente não fala de acidente.
3. **Lê** o que sobrou com o Claude, que decide se é um acidente com navio de carga e extrai navio, data, local, tipo de evento, gravidade e vítimas.
4. **Junta** as notícias que falam do mesmo acidente num único registro.
5. **Avisa** por e-mail quando aparece um acidente muito grave (perda do navio, morte ou poluição grave).

Toda segunda-feira, às 8h de Brasília, ela também manda um relatório com os acidentes da semana.

O plano completo e o registro de decisões ficam no documento do projeto, fora deste repositório.

## Como configurar (uma vez só)

Tudo roda no próprio GitHub (GitHub Actions), sem servidor. Você só precisa cadastrar algumas "senhas" (secrets) no repositório:

**Settings → Secrets and variables → Actions → New repository secret**

| Nome | O que colocar |
| --- | --- |
| `ANTHROPIC_API_KEY` | Chave da API do Claude, criada em [platform.claude.com](https://platform.claude.com) |
| `SMTP_HOST` | Servidor de e-mail. Para Gmail: `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | O endereço de e-mail que vai enviar os avisos |
| `SMTP_PASSWORD` | Para Gmail, uma "senha de app" (não a senha normal): [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) |
| `EMAIL_TO` | Quem recebe os avisos (pode ser o mesmo e-mail) |

Opcional, em **Variables** (não em Secrets): `ACIDMOM_MODEL` troca o modelo do Claude usado na leitura. O padrão é `claude-haiku-5-5`, o mais barato; `claude-opus-5-5` lê melhor textos difíceis, mas custa bem mais.

Depois disso, vá em **Actions → Rastreador → Run workflow** para rodar a primeira vez sem esperar a próxima hora cheia.

## Onde ficam os dados

O banco (um arquivo SQLite) fica no branch `dados` deste repositório, separado do código. Cada execução baixa o banco, atualiza e grava de volta.

## Para quem for mexer no código

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                      # testes, sem acessar a internet
.venv/bin/acidmom rodar               # coleta, processa e avisa (precisa de ANTHROPIC_API_KEY)
.venv/bin/acidmom semanal             # relatório da semana
```

Sem as variáveis de e-mail configuradas, os e-mails aparecem só no log, sem envio.

| Arquivo | Função |
| --- | --- |
| `fontes.toml` | Lista de fontes e buscas |
| `src/acidmom/collectors.py` | Baixa e lê cada fonte |
| `src/acidmom/extract.py` | Filtro por palavras-chave e leitura com o Claude |
| `src/acidmom/dedup.py` | Junta notícias do mesmo acidente |
| `src/acidmom/notify.py` | Aviso imediato e relatório semanal |
| `src/acidmom/db.py` | Banco de dados |
| `.github/workflows/rastreador.yml` | Agendamento |
