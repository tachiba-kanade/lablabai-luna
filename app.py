import chainlit as cl
import os
from dotenv import load_dotenv
load_dotenv()


import requests
import json
import re
import os
from urllib.parse import quote
from dotenv import load_dotenv
load_dotenv()
from config import prompt
from query_index import init_vectara_querier, query_vectara_index

prompt = '''
[
  {"role": "system", "content": "You are a hyghiene consultant, with expertise in women's health and menstrual health management. 
                                 You respond users with a friendly-manner and give them helpful feedbacks for their problems. 
                                 You have a good attitude and good hospitality. "},
  #foreach ($qResult in $vectaraQueryResults)
     {"role": "user", "content": "Give me the $vectaraIdxWord[$foreach.index] search result."},
     {"role": "assistant", "content": "${qResult.getText()}" },
  #end
  {"role": "user", "content": "Generate a summary for the query|'${vectaraQuery}' based on the above results."}
]
'''



def extract_between_tags(text, start_tag, end_tag):
    start_index = text.find(start_tag)
    end_index = text.find(end_tag, start_index)
    return text[start_index+len(start_tag):end_index-len(end_tag)]

class CitationNormalizer():

    def __init__(self, responses, docs):
        self.docs = docs
        self.responses = responses
        self.refs = []

    def normalize_citations(self, summary):
        start_tag = "%START_SNIPPET%"
        end_tag = "%END_SNIPPET%"

        # find all references in the summary
        pattern = r'\[\d{1,2}\]'
        matches = [match.span() for match in re.finditer(pattern, summary)]

        # figure out unique list of references
        for match in matches:
            start, end = match
            response_num = int(summary[start+1:end-1])
            doc_num = self.responses[response_num-1]['documentIndex']
            metadata = {item['name']: item['value'] for item in self.docs[doc_num]['metadata']}
            text = extract_between_tags(self.responses[response_num-1]['text'], start_tag, end_tag)
            if 'url' in metadata.keys():
                url = f"{metadata['url']}#:~:text={quote(text)}"
                if url not in self.refs:
                    self.refs.append(url)

        # replace references with markdown links
        refs_dict = {url:(inx+1) for inx,url in enumerate(self.refs)}
        for match in reversed(matches):
            start, end = match
            response_num = int(summary[start+1:end-1])
            doc_num = self.responses[response_num-1]['documentIndex']
            metadata = {item['name']: item['value'] for item in self.docs[doc_num]['metadata']}
            text = extract_between_tags(self.responses[response_num-1]['text'], start_tag, end_tag)
            if 'url' in metadata.keys():
                url = f"{metadata['url']}#:~:text={quote(text)}"
                citation_inx = refs_dict[url]
                summary = summary[:start] + f'[\[{citation_inx}\]]({url})' + summary[end:]
            else:
                summary = summary[:start] + summary[end:]

        return summary
    
    
class VectaraQuery():
    def __init__(self, api_key: str, customer_id: str, corpus_id: str, prompt_name: str = None, prompt_text: str = None):
        self.customer_id = customer_id
        self.corpus_id = corpus_id
        self.api_key = api_key
        self.prompt_name = prompt_name if prompt_name else "vectara-summary-ext-v1.2.0"
        self.prompt_text = prompt_text

    def get_body(self, query_str: str):
        corpora_key_list = [{
                'customer_id': self.customer_id, 'corpus_id': self.corpus_id, 'lexical_interpolation_config': {'lambda': 0.025}
            }
        ]
        body = {
            'query': [
                { 
                    'query': query_str,
                    'start': 0,
                    'numResults': 50,
                    'corpusKey': corpora_key_list,
                    'context_config': {
                        'sentences_before': 2,
                        'sentences_after': 2,
                        'start_tag': "%START_SNIPPET%",
                        'end_tag': "%END_SNIPPET%",
                    },
                    'rerankingConfig':
                    {
                        'rerankerId': 272725718,
                        'mmrConfig': {
                            'diversityBias': 0.3
                        }
                    },
                    'summary': [
                        {
                            'responseLang': 'eng',
                            'maxSummarizedResults': 5,
                            'summarizerPromptName': self.prompt_name,
                        }
                    ]
                } 
            ]
        }
        if self.prompt_text:
            body['query'][0]['summary'][0]['promptText'] = self.prompt_text
        return body

    def get_headers(self):
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "customer-id": self.customer_id,
            "x-api-key": self.api_key,
            "grpc-timeout": "60S"
        }

    def submit_query(self, query_str: str):

        endpoint = f"https://api.vectara.io/v1/query"
        body = self.get_body(query_str)

        response = requests.post(endpoint, data=json.dumps(body), verify=True, headers=self.get_headers())    
        if response.status_code != 200:
            print(f"Query failed with code {response.status_code}, reason {response.reason}, text {response.text}")
            return "Sorry, something went wrong in my brain. Please try again later."

        res = response.json()
        
        top_k = 10
        summary = res['responseSet'][0]['summary'][0]['text']
        responses = res['responseSet'][0]['response'][:top_k]
        docs = res['responseSet'][0]['document']

        summary = CitationNormalizer(responses, docs).normalize_citations(summary)
        return summary
    
    
def init_vectara_querier(api_key,customer_id,corpus_id,prompt):
    vq = VectaraQuery(api_key, customer_id, corpus_id, 
                  prompt_name = "vectara-experimental-summary-ext-2023-12-11-large", 
                  prompt_text = prompt)
    return vq


def query_vectara_index(vq,query):
    
    response = vq.submit_query(query)
    return response

api_key = os.getenv('VECTARA_API_KEY')
customer_id = os.getenv('VECTARA_CUSTOMER_ID')
corpus_id = os.getenv('VECTARA_CORPUS_ID')


@cl.on_chat_start
def on_chat_start():
    # initialize vectara index
    vectara_index = init_vectara_querier(api_key,customer_id,corpus_id,prompt)
    cl.user_session.set("vectara_index",vectara_index)
    

@cl.on_message
async def on_message(message: cl.Message):
    index = cl.user_session.get("vectara_index")
    query_response = query_vectara_index(index,message.content)

    await cl.Message(content=query_response).send()