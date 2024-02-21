import json
import os
import openai
from openai.types.chat import ChatCompletionMessageToolCall
from pinecone import Pinecone, PodSpec
from dotenv import load_dotenv

from functions import function_map

load_dotenv()

# Setup Pinecone & OpenAI
pc = Pinecone(
    api_key=os.environ.get("PINECONE_API_KEY")
)
index_name = 'function-db'
openai.api_key = os.getenv('OPENAI_API_KEY')
EMBEDDIG_MODEL = "text-embedding-ada-002"

if 'function-db' not in pc.list_indexes().names():
    print('Creating index')
    pc.create_index(index_name, dimension=1536, metric='cosine', spec=PodSpec(
        environment='eu-west1-gcp',
        pod_type='p1.x1',
        pods=1,
        shards=1,
    ))
index = pc.Index(index_name)


def insert_functions(function_list: [dict]):
    print(function_list)
    function_descriptions = [func['function']['description'] for func in function_list]

    # Embed the function descriptions
    embeddings = openai.embeddings.create(
        input=function_descriptions,
        model=EMBEDDIG_MODEL
    ).data
    docs = []
    for i, func in enumerate(function_list):
        doc = (
            func['function']['name'],  # We use the function name as the ID
            embeddings[i].embedding,  # Embedding
            {'name': func['function']['name'],  # Additional metadata, we'll use the function later
             'description': function_descriptions[i],
             'function': json.dumps(func)
             }
        )
        docs.append(doc)
    print(f'Inserting {len(docs)} functions into the index.')
    index.upsert(docs)


def get_functions(query: str, k: int=2, embedding_model=EMBEDDIG_MODEL):
    embedding = openai.embeddings.create(
        input=query,
        model=embedding_model
    ).data[0].embedding
    result = index.query(
        vector=embedding,
        top_k=k,
        include_metadata=True
    )
    return result


def run_function(function_call: ChatCompletionMessageToolCall):
    print('Running function:', function_call.function.name, 'with args:', function_call.function.arguments)
    try:
        function_name = function_call.function.name
        function_to_call = function_map[function_name]
        function_args = json.loads(function_call.function.arguments)
        return function_to_call(function_args)
    except Exception as e:
        print('Error:', e)
        # Handle your exeptions such as invalid function_name etc.

def run_query(query: str, history: [dict]):
    # Step 1: get the functions to use
    functions = get_functions(query=query, k=1).matches
    functions_to_use = [json.loads(func['metadata']['function']) for func in functions] #+ [get_more_functions]

    while True: # Loop until the model decides to stop
        # Step 2: call the model
        response = openai.chat.completions.create(
            model='gpt-4-1106-preview',
            messages=history,
            tools=functions_to_use,
        )
        choice = response.choices[0]
        tool_calls = choice.message.tool_calls

        # Step 3: check if the model wanted to call tools (functions)
        if tool_calls:
            messages.append(choice.message)  # extend conversation with assistant's reply
            # Step 4: call the tools
            for tool_call in tool_calls:
                tool_answer = run_function(tool_call)
                function_name = tool_call.function.name

                messages.append(
                    {
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": str(tool_answer),
                    }
                )
        else:
            print('Finished evaluation')
            messages.append({
                'role': choice.message.role,
                'content': choice.message.content
            })
            return choice.message.content


# Running the app
# Include a system prompt that highlights the ability of function calling
messages = [{'role': 'system',
             'content': 'You are an AI with access to various tools to solve problems. '
                        'You can use these tools and decide in which order to use them. '
                        'You may face complex problems requiring you to use tools either one after the other or at the same time. '
                        'You should not assume the outcome of using a tool without actually using it. '
                        'If needed, you can access more tools to help solve the problem.'}]
while True:
    query = input('Ask question: ')
    messages.append({'role': 'user', 'content': query})
    answer = run_query(query, messages)
    print(answer)
