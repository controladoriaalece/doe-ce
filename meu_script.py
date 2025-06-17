import os
import requests
import smtplib
import datetime
import pypdf
import re # Importando a biblioteca de expressões regulares
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# --- CONFIGURAÇÕES (PREENCHA AQUI) ---
EMAIL_REMETENTE = os.getenv("REMETENTE")
SENHA_REMETENTE = os.getenv("SENHA_APP")
LISTA_DESTINATARIOS = os.getenv("DESTINATARIO")
PASTA_DOWNLOAD = "diarios_pdf"
# --- FIM DAS CONFIGURAÇÕES ---

# --- CONFIGURAÇÕES DA BUSCA ---
FRASE_BUSCA = "Assembleia Legislativa do Estado do Ceará"
TAMANHO_MINIMO_KB = 10
SEPARADOR_PUBLICACAO = "*** *** ***"
CABECALHO_SECAO = "PODER LEGISLATIVO"
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

# --- NOVA FUNÇÃO AUXILIAR ---
def _recortar_publicacao_final(texto_publicacao):
    """
    Recorta o texto da publicação até encontrar os delimitadores.
    Usa expressão regular para encontrar os termos ignorando variações de espaços.
    """
    # Expressão regular que busca os termos com espaços flexíveis entre as palavras
    # e garante que estejam em maiúsculas.
    padrao_tribunal = re.compile(r"TRIBUNAL\s+DE\s+CONTAS\s+DO\s+ESTADO")
    padrao_outros = re.compile(r"OUTROS")

    match_tribunal = padrao_tribunal.search(texto_publicacao)
    match_outros = padrao_outros.search(texto_publicacao)

    posicao_corte = -1

    # Encontra a posição do primeiro delimitador que aparecer no texto
    posicoes = [m.start() for m in [match_tribunal, match_outros] if m]
    if posicoes:
        posicao_corte = min(posicoes)

    if posicao_corte != -1:
        print("Delimitador encontrado. A última publicação será recortada.", flush=True)
        return texto_publicacao[:posicao_corte].strip()
    
    # Se nenhum delimitador for encontrado, retorna o texto original
    return texto_publicacao

# --- FUNÇÃO DE PESQUISA APRIMORADA ---
def pesquisar_nos_pdfs(lista_de_arquivos, frase, separador):
    """
    Pesquisa a frase nos PDFs, extrai as publicações e recorta a última, se necessário.
    """
    print(f"--- Iniciando busca por: '{frase}' ---", flush=True)
    resultados = {}
    frase_lower = frase.lower()

    for caminho_arquivo in lista_de_arquivos:
        nome_arquivo = os.path.basename(caminho_arquivo)
        try:
            texto_completo_pdf = ""
            with open(caminho_arquivo, 'rb') as f:
                reader = pypdf.PdfReader(f)
                for page in reader.pages:
                    texto_completo_pdf += page.extract_text() or "" # Garante que não some None
            
            inicio_secao_idx = texto_completo_pdf.find(CABECALHO_SECAO)

            if inicio_secao_idx != -1:
                print(f"Seção '{CABECALHO_SECAO}' encontrada no arquivo '{nome_arquivo}'.", flush=True)
                texto_da_secao = texto_completo_pdf[inicio_secao_idx:]
                publicacoes_brutas = texto_da_secao.split(separador)
                
                # Filtra apenas as publicações que contêm a frase de busca
                publicacoes_relevantes = [
                    pub.strip() for pub in publicacoes_brutas if frase_lower in pub.lower()
                ]

                if publicacoes_relevantes:
                    # Aplica a lógica de recorte APENAS na última publicação encontrada
                    ultima_publicacao = publicacoes_relevantes[-1]
                    publicacoes_relevantes[-1] = _recortar_publicacao_final(ultima_publicacao)
                    
                    print(f"Encontradas {len(publicacoes_relevantes)} publicações relevantes em '{nome_arquivo}'", flush=True)
                    resultados[nome_arquivo] = publicacoes_relevantes
            else:
                print(f"Seção '{CABECALHO_SECAO}' NÃO encontrada em '{nome_arquivo}'. Arquivo ignorado.", flush=True)

        except Exception as e:
            print(f"ERRO ao ler o arquivo PDF '{caminho_arquivo}': {e}", flush=True)
    
    print(f"--- Fim da busca. Encontrado em {len(resultados)} arquivo(s). ---", flush=True)
    return resultados


def enviar_email(data_formatada, arquivos_anexos, resultados_busca):
    # A função agora usará a lista global LISTA_DESTINATARIOS
    print(f"--- Preparando e-mail para {len(LISTA_DESTINATARIOS)} destinatário(s) em Cópia Oculta (Bcc) ---", flush=True)
    
    msg = MIMEMultipart()
    msg['From'] = EMAIL_REMETENTE
    # O campo "Para" pode ficar vazio, ir para você mesmo, ou ter um texto genérico.
    # Colocar o seu próprio e-mail aqui é uma boa prática.
    msg['To'] = EMAIL_REMETENTE 
    msg['Subject'] = f"📰 Publicações com o termo '{FRASE_BUSCA}' no DOE-CE de {data_formatada} 📅"
    
    # O corpo do e-mail continua o mesmo...
    corpo_email = f"🤖 Olá! \n\nEncontrei as seguintes publicações com o termo '{FRASE_BUSCA}' no Diário Oficial do Estado do Ceará de {data_formatada} 📅.\n\n"
    corpo_email += "================== 📄 PUBLICAÇÕES ENCONTRADAS 📄 ==================\n\n"
    for nome_arquivo, publicacoes in resultados_busca.items():
        corpo_email += f"DO ARQUIVO: {nome_arquivo}\n--------------------------------------------------\n\n"
        for i, pub_texto in enumerate(publicacoes):
            corpo_email += f"PUBLICAÇÃO {i+1}:\n\n{pub_texto}\n\n--------------------------------------------------\n\n"
        corpo_email += "\n"
    corpo_email += f"O(s) arquivo(s) completo(s) do Diário Oficial de {data_formatada} está(ão) em anexo para consulta.\n💡 Caso sinta falta de alguma publicação, por gentileza me comunique em resposta a este e-mail para a melhoria contínua da minha atuação.🦾\n\nAtenciosamente,\n🤖Robô de notificações do DOE-CE📄"
    
    msg.attach(MIMEText(corpo_email, 'plain', 'utf-8'))
    
    # A lógica de anexar arquivos continua a mesma...
    for caminho_arquivo in arquivos_anexos:
        with open(caminho_arquivo, "rb") as f: anexo = MIMEApplication(f.read(), _subtype="pdf")
        nome_arquivo = os.path.basename(caminho_arquivo)
        anexo.add_header('Content-Disposition', 'attachment', filename=nome_arquivo)
        msg.attach(anexo)
        
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_REMETENTE, SENHA_REMETENTE)
        # Usamos o método sendmail para ter controle explícito sobre a lista de destinatários (envelope)
        # Isso garante que a lista não seja visível para os destinatários.
        server.sendmail(EMAIL_REMETENTE, LISTA_DESTINATARIOS, msg.as_string())
        server.quit()
        print(f"E-mail enviado com sucesso em cópia oculta para: {', '.join(LISTA_DESTINATARIOS)}", flush=True)
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
                # --- LÓGICA ALTERADA AQUI ---
                # 1. Pega apenas os nomes dos arquivos que tiveram resultados.
                nomes_dos_arquivos_relevantes = list(resultados.keys())
                
                # 2. Cria a lista completa de caminhos para esses arquivos.
                arquivos_para_anexar = [os.path.join(PASTA_DOWNLOAD, nome) for nome in nomes_dos_arquivos_relevantes]
                
                # 3. Envia o e-mail com a lista de anexos JÁ FILTRADA.
                enviar_email(data_formatada, arquivos_para_anexar, resultados)
            else:
                print("\nAVISO: Os arquivos do diário encontrado não continham o conteúdo relevante.", flush=True)
        finally:
            # A limpeza final SEMPRE remove TODOS os arquivos baixados, para não deixar lixo.
            limpar_arquivos(arquivos_baixados)
    else:
        print(f"\nNenhum diário foi encontrado nos últimos {limite_de_dias_busca} dias. Nenhuma ação será tomada.", flush=True)
        
    print("\n>>> PROCESSO FINALIZADO <<<", flush=True)
