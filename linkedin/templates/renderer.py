# linkedin/actions/template.py
import logging
from pathlib import Path

import jinja2
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, AzureChatOpenAI

from linkedin.conf import (
    AI_MODEL, OPENAI_API_KEY,
    AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT,
    AZURE_DEPLOYMENT_NAME, AZURE_API_VERSION
)

logger = logging.getLogger(__name__)


def call_llm(prompt: str) -> str:
    """Call an LLM to generate content based on the prompt using LangChain and OpenAI."""
    # Check for required values; fail if missing
    # Determine which LLM to use
    if AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT:
        logger.info(f"Calling Azure OpenAI Deployment: '{AZURE_DEPLOYMENT_NAME}'")
        llm = AzureChatOpenAI(
            azure_deployment=AZURE_DEPLOYMENT_NAME,
            openai_api_version=AZURE_API_VERSION,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_KEY,
            temperature=0.7,
        )
    elif OPENAI_API_KEY:
        logger.info(f"Calling OpenAI Model: '{AI_MODEL}'")
        llm = ChatOpenAI(model=AI_MODEL, temperature=0.7, api_key=OPENAI_API_KEY)
    else:
        raise ValueError("Neither OPENAI_API_KEY nor Azure OpenAI credentials are set.")

    # Create a simple prompt template
    chat_prompt = ChatPromptTemplate.from_messages([
        ("human", "{prompt}"),
    ])

    # Chain the prompt with the LLM
    chain = chat_prompt | llm

    # Invoke the chain with the prompt
    response = chain.invoke({"prompt": prompt})

    # Extract the generated content
    return response.content.strip()


def render_template(session: "AccountSession", template_file: str, template_type: str, profile: dict) -> str:
    context = {**profile}

    logger.debug("Available template variables: %s", sorted(context.keys()))

    template_path = Path(template_file)
    folder = template_path.parent  # folder of the template itself
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(folder))
    template = env.get_template(template_path.name)  # load just the filename

    rendered = template.render(**context).strip()
    logger.debug(f"Rendered template: {rendered}")

    match template_type:
        case 'jinja':
            pass
        case 'ai_prompt':
            rendered = call_llm(rendered)
        case _:
            raise ValueError(f"Unknown template_type: {template_type}")

    # Priority:
    # 1. 'job_link' column in CSV
    # 2. 'job_id' column in CSV (constructed)
    # 3. Global 'booking_link' in config
    
    booking_link = profile.get("job_link")
    
    if not booking_link and profile.get("job_id"):
        booking_link = f"https://thescooter.ai/home/careers/{profile['job_id']}"
        
    if not booking_link:
        booking_link = session.config.get("booking_link", None)

    rendered += f"\n{booking_link}" if booking_link else ""
    return rendered
