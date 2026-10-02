import json
import logging
import random
import time
from datetime import datetime, timedelta
from itertools import chain
import os
import functools
from typing import Callable
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import numpy as np
import requests
from dateutil.relativedelta import relativedelta
from fake_useragent import UserAgent
from requests.exceptions import (
    ConnectionError,
    HTTPError,
    RequestException,
    Timeout,
)
from tqdm import tqdm
from bs4 import BeautifulSoup as bs

from utils.settings import Settings

logging.basicConfig(
    level=logging.DEBUG,  # Change to DEBUG for more details
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='detailed_scraper_log.log'
)

settings = Settings()

def with_proxy(func: Callable) -> Callable:
    """
    A decorator that adds a random Oxylabs proxy to any requests made within the decorated function.
    The function being decorated should accept a 'proxies' parameter.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Load proxy config
        oxylabs_config = settings.oxylabs
        OXYLABS_USERNAME = oxylabs_config.get('OXYLABS_USERNAME')
        OXYLABS_PASSWORD = oxylabs_config.get('OXYLABS_PASSWORD')
        
        if not all([OXYLABS_USERNAME, OXYLABS_PASSWORD]):
            raise ValueError("Missing Oxylabs credentials in oxylabs.json")
            
        # Set up proxy with basic auth
        proxy_url = f"http://{OXYLABS_USERNAME}:{OXYLABS_PASSWORD}@pr.oxylabs.io:7777"
        proxies = {
            'http': proxy_url,
            'https': proxy_url
        }
        
        # Add proxies to kwargs if not already present
        if 'proxies' not in kwargs:
            kwargs['proxies'] = proxies
            
        return func(*args, **kwargs)
        
    return wrapper


def get_proxy() -> dict:
    
    oxylabs_config = settings.oxylabs
    
    OXYLABS_USERNAME = oxylabs_config.get("OXYLABS_USERNAME")
    OXYLABS_PASSWORD = oxylabs_config.get('OXYLABS_PASSWORD')
    OXYLABS_ENDPOINTS_HTTPS = oxylabs_config.get('OXYLABS_ENDPOINTS', [])

    # entry = random.choice(OXYLABS_ENDPOINTS_HTTPS)
    entry = random.choice(OXYLABS_ENDPOINTS_HTTPS).replace('https://', 'http://')

    proxies = {
        'https': entry,
        'http': entry
    }

    return proxies


class ComunicaPJE:
    def __init__(self, output_directory=None):
        """

        Args:
            dataDisponibilizacaoInicio: Must be follow this pattern
                                        'dd-mm-YYYY'. Example: '31-12-2024'.
        """
        self.dataDisponibilizacaoInicio = None
        self.dataDisponibilizacaoFim = None
        self.tribunais = ['TJRJ', 'TJMG', 'TRF2', 'TRF3', 'STJ', 'TJSC']
        self.ua = UserAgent()
        self.output_directory = output_directory or os.getcwd()
        self.proxies = get_proxy()


    def break_dates(self, factor: str) -> list:
        """
        Function break the range of dates of scraping in days, weeks, months or years.

        Args:
            factor: Decides the factor of dividing the dates to output the
                    list.
                    'days' : For output list of days ranges
                    'weeks' : For output list of weeks ranges
                    'months' : For output list of months ranges
                    'years' : For output list of years ranges

        Returns:
            list: A list of tuples, where each tuple represents the start and end of a range.
        """
        data_inicio = datetime.strptime(
            self.dataDisponibilizacaoInicio, '%d-%m-%Y'
        )
        data_fim = datetime.strptime(self.dataDisponibilizacaoFim, '%d-%m-%Y')

        # Define the increment based on the factor
        increments = {
            'days': timedelta(days=1),
            'weeks': timedelta(weeks=1),
            'months': relativedelta(months=1),
            'years': relativedelta(years=1),
        }

        increment = increments[factor]

        # Generate the ranges
        ranges = []
        current_start = data_inicio

        while current_start < data_fim:
            current_end = min(
                current_start + increment - timedelta(days=1), data_fim
            )
            ranges.append(
                (
                    current_start.strftime('%d-%m-%Y'),
                    current_end.strftime('%d-%m-%Y'),
                )
            )
            current_start += increment

        return ranges

    def exponential_backoff(attempt, base_delay=5, max_delay=90):
        """
        Calculate the exponential backoff delay with jitter.
        """
        delay = min(max_delay, base_delay * (2**attempt))
        return delay + random.uniform(0, 1)

    # @with_proxy
    def get_page(
        self,
        data_inicio: str,
        data_fim: str,
        pagina: int,
        sigla_tribunal: str = None,
        nomeParte: str = None,
        assunto: str = None,
        num_processo: str = None,
        count: bool = False,
        proxies: dict = None,
    ) -> dict:
        """
        Fetch a specific page with retries, timeout, and exponential backoff.
        Logs errors cleanly without breaking tqdm display.
        """
        # random_user_agent = self.ua.random
        url = 'https://comunicaapi.pje.jus.br/api/v1/comunicacao'

        headers = {
            'authority': 'comunicaapi.pje.jus.br',
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en-US,en;q=0.9',
            'origin': 'https://comunica.pje.jus.br',
            'referer': 'https://comunica.pje.jus.br/',
            'sec-ch-ua': '"Google Chrome";v="119", "Chromium";v="119", "Not?A_Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
        }

        params = {
            'pagina': str(pagina),
            'itensPorPagina': '100' if not count else '5',
            'siglaTribunal': sigla_tribunal,
            'dataDisponibilizacaoInicio': data_inicio,
            'dataDisponibilizacaoFim': data_fim,
        }

        if nomeParte is not None:
            params['nomeParte'] = nomeParte
        if assunto:
            params['texto'] = assunto
        if num_processo:
            params['numeroProcesso'] = num_processo

        retries = 2
        backoff_factor = 5

        for attempt in range(1, retries + 1):
            try:
                # Try without proxy first
                current_proxies = None if attempt == 1 else proxies
                if current_proxies:
                    logging.debug(f"Attempt {attempt} with proxy")
                else:
                    logging.debug(f"Attempt {attempt} without proxy")
                    
                full_url = f"{url}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
                logging.debug(f"Request URL: {full_url}")
                
                response = requests.get(
                    url=url, 
                    headers=headers, 
                    params=params, 
                    timeout=120, 
                    # proxies=proxies, 
                    verify=True
                )
                
                logging.debug(f"Response status: {response.status_code}")
                logging.debug(f"Response headers: {dict(response.headers)}")
                
                response.raise_for_status()
                return response.json()
            except Timeout:
                error_message = f'[Timeout] Attempt {attempt}: Page {pagina} for {data_inicio} - {data_fim} timed out.'
                tqdm.write(error_message)
                logging.error(error_message)
            except HTTPError as e:
                error_message = f'[HTTP Error] Attempt {attempt}: {e.response.status_code} on page {pagina}. Skipping.'
                tqdm.write(error_message)
                logging.error(error_message)
                if (
                    e.response.status_code == 403
                ):  # Don't retry on 403 Forbidden
                    break
            except ConnectionError as e:
                error_message = f'[Connection Error] Attempt {attempt}: Page {pagina} for {data_inicio} - {data_fim}.'
                if hasattr(e, 'request'):
                    error_message += f" | Request: {e.request.method} {getattr(e.request, 'url', 'No URL')}"
                if hasattr(e, 'response') and e.response is not None:
                    error_message += f" | Status: {e.response.status_code} | Response: {getattr(e.response, 'text', 'No response text')[:200]}"
                tqdm.write(error_message)
                logging.error(error_message, exc_info=True)  # Include full traceback
            except RequestException as e:
                error_message = f'[Request Error] Attempt {attempt}: Page {pagina} error: {e}.'
                tqdm.write(error_message)
                logging.error(error_message)

            except Exception as e:
                logging.error(f"Proxy attempt {attempt} error: {e}")
                
            # Exponential backoff
            if attempt < retries:
                sleep_time = backoff_factor**attempt
                error_message = f'[Retrying] Retrying page {pagina} in {sleep_time} seconds...'
                tqdm.write(error_message)
                logging.error(error_message)
                time.sleep(sleep_time)
            else:
                error_message = f'[Failed] Max retries reached for page {pagina} ({data_inicio} - {data_fim}).'
                tqdm.write(error_message)
                logging.error(error_message)

        raise ValueError(
            f'Failed to fetch page {pagina} for {data_inicio} - {data_fim} after {retries} attempts.'
        )

    def get_batch_old(
        self,
        data_inicio: str,
        data_fim: str,
        problematic_collection: list,
        sigla_tribunal: str = None,
        nomeParte: str = None,
        assunto: str = None,
    ) -> None:
        try:
            first_page_response = self.get_page(
                data_inicio=data_inicio,
                data_fim=data_fim,
                sigla_tribunal=sigla_tribunal,
                pagina=1,
                count=True,
                assunto=assunto,
            )
            n_pages = int(np.ceil(first_page_response['count'] / 100))

            with tqdm(
                total=n_pages,
                desc=f'Fetching pages ({assunto}): {data_inicio}-{data_fim}',
                unit='page',
            ) as pbar:
                for page in range(1, n_pages + 1):
                    if page % 100 == 0:
                        time.sleep(10)
                    try:
                        page_data = self.get_page(
                            data_inicio=data_inicio,
                            data_fim=data_fim,
                            sigla_tribunal=sigla_tribunal,
                            pagina=page,
                            nomeParte=nomeParte,
                            assunto=assunto,
                        )
                        # Save raw JSON
                        start_date = datetime.strptime(data_inicio, '%d-%m-%Y')
                        date_folder = start_date.strftime('%Y%m%d')
                        assunto_folder = assunto.replace(' ', '_')
                        folder_path = os.path.join('raw', assunto_folder, date_folder)
                        os.makedirs(folder_path, exist_ok=True)
                        filename = os.path.join(folder_path, f'page_{page}.json')
                        with open(filename, 'w', encoding='utf-8') as f:
                            json.dump(page_data, f, ensure_ascii=False, indent=4)
                        tqdm.write(f'Saved page {page} to {filename}')
                    except ValueError as e:
                        error_msg = f'[Error] Skipping page {page}: {str(e)}'
                        logging.error(error_msg)
                        tqdm.write(error_msg)
                        problematic_collection.append({
                            'data_inicio': data_inicio,
                            'data_fim': data_fim,
                            'page': page,
                            'error': str(e),
                        })
                    pbar.update(1)
        
        except Exception as e:
            error_msg = f'[Critical Error] Failed: {str(e)}'
            logging.error(error_msg)
            tqdm.write(error_msg)
            problematic_collection.append({
                'data_inicio': data_inicio,
                'data_fim': data_fim,
                'error': str(e),
            })


    def get_batch(
            self,
            data_inicio: str,
            data_fim: str,
            problematic_collection: list,
            sigla_tribunal: str = None,
            nomeParte: str = None,
            assunto: str = None,
            frequency: str = 'days',
            num_processo: str = None,
            assunto_safe: str = 'sem_assunto',
        ):  # Remove output_dir parameter
            try:
                # Add a progress message showing the period range
                tqdm.write(f'Processing {frequency.rstrip("s")} from {data_inicio} to {data_fim}')
                
                first_page_response = self.get_page(
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    sigla_tribunal=sigla_tribunal,
                    pagina=1,
                    count=True,
                    assunto=assunto,
                    num_processo=num_processo,
                    )

                # Enhanced check for empty results - RETURN EARLY if no results
                if (
                    first_page_response.get('count', 0) == 0 or 
                    not first_page_response.get('items') or 
                    len(first_page_response.get('items', [])) == 0
                ):
                    tqdm.write(f'No results found for {data_inicio} to {data_fim} - Skipping file creation')
                    
                    # Add to processed ranges but don't create files
                    return 0

                n_pages = int(np.ceil(first_page_response.get('count', 0) / 100))

                # Adjust rate limiting based on frequency
                rate_limits = {
                    'days': {'batch': 100, 'delay': 5},
                    'weeks': {'batch': 100, 'delay': 5},
                    'months': {'batch': 100, 'delay': 5},
                    'years': {'batch': 100, 'delay': 5}
                }
                rate_limit = rate_limits.get(frequency, {'batch': 100, 'delay': 10})

                # Track consecutive empty pages
                consecutive_empty_pages = 0
                max_consecutive_empty_pages = 2  # Configurable threshold


                with tqdm(
                    total=n_pages,
                    desc=f'{frequency.rstrip("s").title()} {data_inicio} to {data_fim} ({assunto})',
                    unit='page',
                ) as pbar:
                    for page in range(1, n_pages + 1):
                        if page % rate_limit['batch'] == 0:
                            time.sleep(rate_limit['delay'])
                        try:
                                page_data = self.get_page(
                                    data_inicio=data_inicio,
                                    data_fim=data_fim,
                                    sigla_tribunal=sigla_tribunal,
                                    pagina=page,
                                    nomeParte=nomeParte,
                                    assunto=assunto,
                                    num_processo=num_processo,
                                )
                                if page_data.get('count', 0) > 0 and len(page_data.get('items', [])) > 0:
                                    # Reset consecutive empty pages counter
                                    consecutive_empty_pages = 0
                                    
                                    # Create frequency-based folder structure
                                    start_date = datetime.strptime(data_inicio, '%d-%m-%Y')
                                    if frequency == 'days':
                                        period_folder = start_date.strftime('%Y%m%d')
                                    elif frequency == 'weeks':
                                        period_folder = start_date.strftime('%Y_W%W')
                                    elif frequency == 'months':
                                        period_folder = start_date.strftime('%Y%m')
                                    else:  # years
                                        period_folder = start_date.strftime('%Y')

                                    folder_path = os.path.join(self.output_directory, period_folder)
                                    os.makedirs(folder_path, exist_ok=True)
                                    
                                    assunto_folder = assunto_safe.replace(' ', '_').lower()
                                    filename = os.path.join(folder_path, f'{assunto_folder}_{frequency}_page{page}.json')
                                    with open(filename, 'w', encoding='utf-8') as f:
                                        json.dump(page_data, f, ensure_ascii=False, indent=4)
                                    tqdm.write(f'Saved page {page} to {filename}')
                                else:
                                    # Increment consecutive empty pages counter
                                    consecutive_empty_pages += 1
                                    tqdm.write(f'Page {page} has no items')
                                    
                                    # Check if we've hit the max consecutive empty pages
                                    if consecutive_empty_pages >= max_consecutive_empty_pages:
                                        tqdm.write(f'Reached {consecutive_empty_pages} consecutive empty pages. Stopping batch.')
                                        break
                                    
                        except ValueError as e:
                            error_msg = f'[Error] Skipping page {page}: {str(e)}'
                            logging.error(error_msg)
                            tqdm.write(error_msg)
                            problematic_collection.append({
                                'data_inicio': data_inicio,
                                'data_fim': data_fim,
                                'page': page,
                                'frequency': frequency,
                                'error': str(e),
                            })
                        pbar.update(1)

                return n_pages

            except Exception as e:
                error_msg = f'[Critical Error] Failed for {frequency} {data_inicio} to {data_fim}: {str(e)}'
                logging.error(error_msg)
                tqdm.write(error_msg)
                problematic_collection.append({
                    'data_inicio': data_inicio,
                    'data_fim': data_fim,
                    'frequency': frequency,
                    'error': str(e),
                })
                return 0


    def load_progress(self, filename: str) -> list:
        """
        Load the list of processed ranges from a progress file.
        """
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []  # Return empty list if no progress file exists

    def save_progress(self, filename: str, processed_ranges: list):
        """
        Save the list of processed ranges to a progress file.
        """
        try:
            with open(filename, 'w') as f:
                json.dump(processed_ranges, f, indent=4)
        except Exception as e:
            logging.error(f'Failed to save progress to {filename}: {e}')


    def get_tribunal_old(
        self,
        batch_size: int = 100,
        sigla_tribunal: str = None,
        nomeParte: str = None,
        assunto: str = None,
        output_dir: str = 'data_output',
    ):
        dates = self.break_dates('days')
        problematic_collection = []

        progress_file = f'{assunto}_progress.json'
        processed_ranges = self.load_progress(progress_file)

        try:
            with open(f'{assunto}_batches.json', 'a+') as batch_f:
                for date in tqdm(dates, desc=f'Processing {assunto}', unit='date'):
                    data_inicio, data_fim = date
                    if f'{data_inicio}_{data_fim}' in processed_ranges:
                        continue

                    self.get_batch(
                        data_inicio=data_inicio,
                        data_fim=data_fim,
                        sigla_tribunal=sigla_tribunal,
                        problematic_collection=problematic_collection,
                        nomeParte=nomeParte,
                        assunto=assunto,
                        output_dir=output_dir,
                    )

                    processed_ranges.append(f'{data_inicio}_{data_fim}')
                    self.save_progress(progress_file, processed_ranges)

        except Exception as e:
            logging.error(f'Failed processing {assunto}: {e}')

        return problematic_collection


    def get_tribunal(
        self,
        frequency: str = 'days',
        batch_size: int = 100,
        sigla_tribunal: str = None,
        nomeParte: str = None,
        assunto: str = None,
        num_processo: str = None,
        nome_arquivo: str = None,
    ):  
        # Validate frequency parameter
        valid_frequencies = ['days', 'weeks', 'months', 'years']
        if frequency not in valid_frequencies:
            raise ValueError(f"Frequency must be one of {valid_frequencies}")
        
        dates = self.break_dates(frequency)
        problematic_collection = []
        total_pages = 0

        # Use self.output_directory for progress files
        progress_dir = os.path.join(self.output_directory, "metadata", "progress")
        os.makedirs(progress_dir, exist_ok=True)
        assunto_safe = (
            nome_arquivo.replace(' ', '_')
            if nome_arquivo
            else assunto.replace(' ', '_')  if assunto
            else 'sem_assunto'
        )
        progress_file = os.path.join(progress_dir, f'{assunto_safe}_{frequency}_progress.json')
        processed_ranges = self.load_progress(progress_file)

        try:
            freq_desc = frequency.rstrip('s')
            
            for date in tqdm(dates, desc=f'Processing {assunto}', unit=freq_desc):
                data_inicio, data_fim = date
                range_key = f'{data_inicio}_{data_fim}'
                
                if range_key in processed_ranges:
                    tqdm.write(f'Skipping already processed {freq_desc}: {data_inicio} to {data_fim}')
                    continue

                # Modify get_batch to return number of pages processed
                batch_pages = self.get_batch(
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    sigla_tribunal=sigla_tribunal,
                    problematic_collection=problematic_collection,
                    nomeParte=nomeParte,
                    assunto=assunto,
                    num_processo=num_processo,
                    frequency=frequency,
                    assunto_safe=assunto_safe
                )
                
                # Accumulate total pages
                total_pages += batch_pages

                # Save progress
                processed_ranges.append(range_key)
                self.save_progress(progress_file, processed_ranges)

                # Optional: Add a small delay between frequency periods
                if frequency in ['weeks', 'months', 'years']:
                    time.sleep(2)  # Longer periods might need more

        except Exception as e:
                error_msg = f'Failed processing {assunto} with {frequency} frequency: {str(e)}'
                logging.error(error_msg)
                tqdm.write(error_msg)
                problematic_collection.append({
                    'data_inicio': data_inicio,
                    'data_fim': data_fim,
                    'frequency': frequency,
                    'error': str(e),
                })

        # Save final statistics
        stats_file = os.path.join(
            progress_dir,
            f'{assunto_safe}_{frequency}_stats.json'
        )
        try:
            stats = {
                'frequency': frequency,
                'total_pages': total_pages,
                'total_periods': len(dates),
                'completed_periods': len(processed_ranges),
                'problematic_periods': len(problematic_collection),
                'start_date': self.dataDisponibilizacaoInicio,
                'end_date': self.dataDisponibilizacaoFim,
                'completion_timestamp': datetime.now().isoformat()
            }
            with open(stats_file, 'w') as f:
                json.dump(stats, f, indent=4)
        except Exception as e:
            logging.error(f'Failed to save statistics: {e}')

        return problematic_collection, total_pages


class FastMedicamentos:
    def setup_driver():
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
        
        driver.get('https://www.fastmedicamentos.com.br/loja/')
        
        time.sleep(4)
        
        return driver
        
    def click_on_plus_button(driver):
        try:
            # Locate the parent div
            div = driver.find_element(By.CLASS_NAME, 'premium-woo-load-more')
            # Locate the button inside the div
            button = div.find_element(By.CLASS_NAME, 'premium-woo-load-more-btn')
            # Click the button
            button.click()
            return True  # Return True if the button was successfully clicked
        except NoSuchElementException:
            print("Button or parent div not found. Exiting function.")
            return False  # Return False if the button does not exist
        except ElementClickInterceptedException:
            print("Button is not clickable (blocked by another element).")
            return False  # Return False if the button could not be clicked


    def get_meds_urls(self, driver):
        div = driver.find_element(By.CLASS_NAME, 'premium-woo-load-more')
        # Locate the button inside the div
        button = div.find_element(By.CLASS_NAME, 'premium-woo-load-more-btn')
        
        rounds = round(int(button.text[-4:][:-1]) / 20) + 5 
        
        for turn in range(rounds):
            self.click_on_plus_button(driver)
            time.sleep(10)
        
        source = driver.page_source

        soup = bs(source, 'html.parser')
        
        list = soup.find(class_='products columns-4')
        
        raw_items = list.find_all('li')

        items = []

        for item in raw_items:
            nome = item.find('h2').text
            link = item.find('a').get('href')

            items.append({'nome': nome, 'link': link})

        return items


    def get_med(driver, link):
        driver.get(link)
        
        time.sleep(5)
        
        source = driver.page_source
        
        soup = bs(source, 'html.parser')
        
        nome = soup.find_all("h1")[0]
        preco = soup.find("span", class_="woocommerce-Price-currencySymbol").next_sibling.strip()
        principio_ativo_parent = soup.find("tr", class_="woocommerce-product-attributes-item woocommerce-product-attributes-item--attribute_pa_principio-ativo")
        principio_ativo = principio_ativo_parent.find("td", class_="woocommerce-product-attributes-item__value").text.strip()
        fabricante_parent = soup.find('tr', class_='woocommerce-product-attributes-item woocommerce-product-attributes-item--attribute_pa_fabricante')
        if fabricante_parent != None:
            fabricante= fabricante_parent.find('td', class_='woocommerce-product-attributes-item__value').text.strip()
        else:
            fabricante = None
        ean = soup.find('span', class_='sku').text.strip()
        categorias_raw = soup.find('span', class_='detail-content')
        categorias = []
        for a in categorias_raw.find_all('a'):
            categorias.append(a.text.strip())
            
        detalhes = soup.find('div', class_='elementor-element elementor-element-25dfb58 e-flex e-con-boxed e-con e-parent e-lazyloaded').text.strip()

        dict = {
            'nome': nome,
            'preco': preco,
            'principio_ativo': principio_ativo,
            'fabricante': fabricante,
            'ean': ean,
            'categorias': categorias,
            'detalhes': detalhes,
            'link': link
        }
