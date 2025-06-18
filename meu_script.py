import os
import requests
import smtplib
import datetime
import pypdf
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# --- CONFIGURAÇÕES (CARREGADAS DOS SECRETS) ---

# Carrega as variáveis de ambiente passadas pelo GitHub Actions
EMAIL_REMETENTE = os.getenv("REMETENTE")
SENHA_REMETENTE = os.getenv("SENHA_APP")

# --- LÓGICA ATUALIZADA PARA MÚLTIPLOS DESTINATÁRIOS ---
# 1. Pega a string de e-mails (ex: "email1@a.com,email2@b.com") do Secret.
destinatarios_str = os.getenv("DESTINATARIOS")

# 2. Converte a string em uma lista de e-mails, tratando o caso de não estar definida.
# A função strip() em cada e-mail remove espaços em branco acidentais.
if destinatarios_str:
    LISTA_DESTINATARIOS = [email.strip() for email in destinatarios_str.split(',')]
else:
    LISTA_DESTINATARIOS = [] # Garante que a lista fique vazia se o secret não for configurado.
# --- FIM DA LÓGICA ATUALIZADA ---

PASTA_DOWNLOAD = "diarios_pdf"
# --- FIM DAS CONFIGURAÇÕES ---

# --- CONFIGURAÇÕES DA BUSCA ---
FRASE_BUSCA = "Assembleia Legislativa do Estado do Ceará"
TAMANHO_MINIMO_KB = 10
SEPARADOR_PUBLICACAO = "*** *** ***"
# --- FIM DA CONFIGURAÇÃO DA BUSCA ---


def baixar_diarios(data_str):
    """Baixa todas as páginas do Diário Oficial para uma data específica."""
    print(f"--- Tentando download completo para data: {data_str} ---", flush=True)
    base_url = "http://imagens.seplag.ce.gov.br/pdf"
    arquivos_validos = []
    tamanho_minimo_bytes = TAMANHO_MINIMO_KB * 1024
    
    if not os.path.exists(PASTA_DOWNLOAD):
        os.makedirs(PASTA_DOWNLOAD)
        print(f"Pasta '{PASTA_DOWNLOAD}' criada.", flush=True)
    
    for i in range(1, 201):
        numero_pagina_str = str(i).zfill(2)
        nome_arquivo = f"do{data_str}p{numero_pagina_str}.pdf"
        url_completa = f"{base_url}/{data_str}/{nome_arquivo}"
        caminho_local = os.path.join(PASTA_DOWNLOAD, nome_arquivo)
        
        try:
            response = requests.get(url_completa, timeout=15)
            if response.status_code == 404:
                print(f"Página {numero_pagina_str} não encontrada. Fim dos diários do dia.", flush=True)
                break
            response.raise_for_status()
            
            with open(caminho_local, 'wb') as f:
                f.write(response.content)
            
            tamanho_arquivo = os.path.getsize(caminho_local)
            if tamanho_arquivo > tamanho_minimo_bytes:
                arquivos_validos.append(caminho_local)
                print(f"Sucesso: '{nome_arquivo}' baixado ({tamanho_arquivo/1024:.2f} KB).", flush=True)
            else:
                print(f"Ignorado: '{nome_arquivo}' é muito pequeno ({tamanho_arquivo/1024:.2f} KB).", flush=True)
                os.remove(caminho_local)
                print("Assumindo que não há mais páginas válidas. Interrompendo downloads.", flush=True)
                break
                
        except requests.exceptions.RequestException as e:
            print(f"ERRO ao baixar {url_completa}: {e}", flush=True)
            break
            
    print(f"--- Fim do download. Total de arquivos válidos: {len(arquivos_validos)} ---", flush=True)
    return arquivos_validos

def _recortar_publicacao_final(texto_publicacao):
    """Recorta o texto da publicação até encontrar os delimitadores."""
    padrao_tribunal = re.compile(r"TRIBUNAL\s+DE\s+CONTAS\s+DO\s+ESTADO")
    padrao_outros = re.compile(r"OUTROS")

    match_tribunal = padrao_tribunal.search(texto_publicacao)
    match_outros = padrao_outros.search(texto_publicacao)

    posicoes = [m.start() for m in [match_tribunal, match_outros] if m]
    if posicoes:
        posicao_corte = min(posicoes)
        print("Delimitador encontrado. A última publicação será recortada.", flush=True)
        return texto_publicacao[:posicao_corte].strip()
    
    return texto_publicacao

# --- FUNÇÃO DE PESQUISA COM IDENTIFICAÇÃO DE SEÇÃO ---
def pesquisar_nos_pdfs(lista_de_arquivos, frase, separador):
    """
    Pesquisa a frase em todo o conteúdo, identifica se a publicação está dentro ou fora
    da seção principal, e retorna os resultados com essa informação.
    """
    print(f"--- Iniciando busca inteligente por: '{frase}' ---", flush=True)
    resultados = {}
    frase_lower = frase.lower()

    for caminho_arquivo in lista_de_arquivos:
        nome_arquivo = os.path.basename(caminho_arquivo)
        try:
            texto_completo_pdf = ""
            with open(caminho_arquivo, 'rb') as f:
                reader = pypdf.PdfReader(f)
                if reader.is_encrypted: reader.decrypt('')
                for page in reader.pages:
                    texto_extraido = page.extract_text()
                    if texto_extraido:
                        texto_completo_pdf += texto_extraido + "\n"

            publicacoes_brutas = texto_completo_pdf.split(separador)
            publicacoes_encontradas = []
            
            # Encontra a posição do cabeçalho para saber o que é "dentro" e o que é "fora"
            inicio_secao_idx = texto_completo_pdf.find(CABECALHO_SECAO)

            for pub_texto in publicacoes_brutas:
                if frase_lower in pub_texto.lower():
                    # Encontra a posição da publicação atual no texto completo
                    posicao_pub = texto_completo_pdf.find(pub_texto)
                    
                    localizacao = "Em Outras Seções" # Padrão
                    if inicio_secao_idx != -1 and posicao_pub >= inicio_secao_idx:
                        localizacao = "Na Seção do Poder Legislativo"

                    # Adiciona um dicionário com o texto e a localização
                    publicacoes_encontradas.append({
                        "texto": pub_texto.strip(),
                        "local": localizacao
                    })

            if publicacoes_encontradas:
                # A lógica de recortar a última publicação pode ser removida ou ajustada
                # se não for mais necessária com esta nova abordagem.
                # Por enquanto, vamos mantê-la para a última publicação encontrada.
                ultimo_dict = publicacoes_encontradas[-1]
                ultimo_dict["texto"] = _recortar_publicacao_final(ultimo_dict["texto"])
                
                print(f"Encontradas {len(publicacoes_encontradas)} publicações relevantes em '{nome_arquivo}'", flush=True)
                resultados[nome_arquivo] = publicacoes_encontradas

        except Exception as e:
            print(f"ERRO ao ler o arquivo PDF '{caminho_arquivo}': {e}", flush=True)
    
    print(f"--- Fim da busca. Encontrado em {len(resultados)} arquivo(s). ---", flush=True)
    return resultados


def enviar_email(data_formatada, arquivos_anexos, resultados_busca):
    """Envia um e-mail formatado com HTML, organizando os resultados por seção."""
    print(f"--- 📧 Preparando e-mail com os resultados de {data_formatada} ---", flush=True)
    msg = MIMEMultipart()
    msg['From'] = EMAIL_REMETENTE
    msg['To'] = ", ".join(LISTA_DESTINATARIOS) # Usando a lista para o campo "Para"
    msg['Subject'] = f"📰 Publicações da Assembleia Legislativa no Diário Oficial de {data_formatada}"
    
    # --- CONSTRUÇÃO DO CORPO DO E-MAIL EM HTML ---
    corpo_email = f"""
    <html>
      <head></head>
      <body>
        <p>Olá! 👋</p>
        <p>Seu robô 🤖 <b>encontrou as seguintes publicações</b> com o termo <i>'{FRASE_BUSCA}'</i> no Diário Oficial de <b>{data_formatada}</b>.</p>
        <hr>
    """
    
    for nome_arquivo, publicacoes in resultados_busca.items():
        corpo_email += f"<h2>DO ARQUIVO: <b>{nome_arquivo}</b></h2>"
        
        # Separa as publicações por localização
        pubs_na_secao = [p for p in publicacoes if p['local'] == "Na Seção do Poder Legislativo"]
        pubs_fora_secao = [p for p in publicacoes if p['local'] == "Em Outras Seções"]

        if pubs_na_secao:
            corpo_email += "<h3>Na Seção do Poder Legislativo:</h3>"
            for i, pub_dict in enumerate(pubs_na_secao):
                corpo_email += f"<p><b>PUBLICAÇÃO {i+1}:</b></p>"
                corpo_email += f"<pre style='white-space: pre-wrap; word-wrap: break-word; background-color: #f4f4f4; padding: 10px; border-radius: 5px;'>{pub_dict['texto']}</pre>"
        
        if pubs_fora_secao:
            corpo_email += "<h3>Em Outras Seções:</h3>"
            for i, pub_dict in enumerate(pubs_fora_secao):
                corpo_email += f"<p><b>PUBLICAÇÃO {i+1}:</b></p>"
                corpo_email += f"<pre style='white-space: pre-wrap; word-wrap: break-word; background-color: #f0f8ff; padding: 10px; border-radius: 5px;'>{pub_dict['texto']}</pre>"

        corpo_email += "<br>"

    corpo_email += f"""
        <hr>
        <p>As páginas do Diário Oficial contendo estas publicações estão em anexo para consulta. ✅</p>
        <p>Atenciosamente,<br><b>Seu Robô 🤖</b></p>
      </body>
    </html>
    """
    
    msg.attach(MIMEText(corpo_email, 'html', 'utf-8'))
    
    # Lógica de anexos continua a mesma, anexando apenas os arquivos relevantes
    for caminho_arquivo in arquivos_anexos:
        with open(caminho_arquivo, "rb") as f:
            anexo = MIMEApplication(f.read(), _subtype="pdf")
        nome_arquivo = os.path.basename(caminho_arquivo)
        anexo.add_header('Content-Disposition', 'attachment', filename=nome_arquivo)
        msg.attach(anexo)
        
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_REMETENTE, SENHA_REMETENTE)
        server.sendmail(EMAIL_REMETENTE, LISTA_DESTINATARIOS, msg.as_string())
        server.quit()
        print(f"E-mail enviado com sucesso!", flush=True)
    except Exception as e:
        print(f"ERRO CRÍTICO ao enviar o e-mail: {e}", flush=True)

def limpar_arquivos(lista_de_arquivos):
    """Remove os arquivos PDF baixados."""
    if not lista_de_arquivos: return
    print("--- Limpando arquivos temporários... ---", flush=True)
    for arquivo in lista_de_arquivos:
        try: 
            os.remove(arquivo)
        except OSError as e:
            print(f"Erro ao remover o arquivo {arquivo}: {e}", flush=True)
    try:
        if os.path.exists(PASTA_DOWNLOAD) and not os.listdir(PASTA_DOWNLOAD):
            os.rmdir(PASTA_DOWNLOAD)
    except OSError as e:
        print(f"Erro ao remover a pasta {PASTA_DOWNLOAD}: {e}", flush=True)

# =================================================================================
# BLOCO DE EXECUÇÃO PRINCIPAL
# =================================================================================
if __name__ == "__main__":
    print(">>> INICIANDO ROBÔ DE BUSCA NO DIÁRIO OFICIAL DO CEARÁ <<<")
    
    # Verificação inicial de segurança
    if not all([EMAIL_REMETENTE, SENHA_REMETENTE, LISTA_DESTINATARIOS]):
        print("\nERRO FATAL: Variáveis de ambiente (REMETENTE, SENHA_APP, DESTINATARIOS) não configuradas corretamente. Abortando.", flush=True)
    else:
        arquivos_baixados = []
        data_sucesso = None
        limite_de_dias_busca = 15

        for i in range(limite_de_dias_busca):
            data_alvo = datetime.date.today() - datetime.timedelta(days=i)
            data_str = data_alvo.strftime("%Y%m%d")
            
            print(f"\n--- TENTANDO DATA: {data_alvo.strftime('%d/%m/%Y')} ---", flush=True)
            
            arquivos_baixados = baixar_diarios(data_str)
            
            if arquivos_baixados:
                data_sucesso = data_alvo
                print(f">>> SUCESSO! Diário encontrado para {data_sucesso.strftime('%d/%m/%Y')}. Prosseguindo com a análise.", flush=True)
                break
            else:
                print(f">>> Nenhum diário válido encontrado para esta data. Tentando dia anterior...", flush=True)

        if arquivos_baixados and data_sucesso:
            try:
                resultados = pesquisar_nos_pdfs(arquivos_baixados, FRASE_BUSCA, SEPARADOR_PUBLICACAO)
                data_formatada = data_sucesso.strftime('%d/%m/%Y')
                
                if resultados:
                    nomes_dos_arquivos_relevantes = list(resultados.keys())
                    arquivos_para_anexar = [os.path.join(PASTA_DOWNLOAD, nome) for nome in nomes_dos_arquivos_relevantes]
                    enviar_email(data_formatada, arquivos_para_anexar, resultados)
                else:
                    print("\nAVISO: Os arquivos do diário encontrado não continham o conteúdo relevante.", flush=True)
            finally:
                limpar_arquivos(arquivos_baixados)
        else:
            print(f"\nNenhum diário foi encontrado nos últimos {limite_de_dias_busca} dias. Nenhuma ação será tomada.", flush=True)
            
    print("\n>>> PROCESSO FINALIZADO <<<", flush=True)
