"""Module implements an agent that uses OpenAI's APIs function enabled API."""
from typing import Any, List, Optional, Sequence, Tuple, Union

from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
)
from langchain_core.prompts import BasePromptTemplate
from langchain_core.prompts.chat import (
    BaseMessagePromptTemplate,
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    MessagesPlaceholder,
)
from langchain_core.pydantic_v1 import root_validator
from langchain_core.runnables import RunnablePassthrough
from langchain_core.tools import BaseTool

from langchain.agents import BaseSingleActionAgent
from langchain.agents.format_scratchpad.openai_functions import (
    format_to_openai_function_messages,
)
from langchain.agents.output_parsers.openai_functions import (
    OpenAIFunctionsAgentOutputParser,
)
from langchain.callbacks.base import BaseCallbackManager
from langchain.callbacks.manager import Callbacks
from langchain.tools.render import format_tool_to_openai_function


class OpenAIFunctionsAgent(BaseSingleActionAgent):
    """An Agent driven by OpenAIs function powered API.

    Args:
        llm: This should be an instance of ChatOpenAI, specifically a model
            that supports using `functions`.
        tools: The tools this agent has access to.
        prompt: The prompt for this agent, should support agent_scratchpad as one
            of the variables. For an easy way to construct this prompt, use
            `OpenAIFunctionsAgent.create_prompt(...)`
    """

    llm: BaseLanguageModel
    tools: Sequence[BaseTool]
    prompt: BasePromptTemplate

    def get_allowed_tools(self) -> List[str]:
        """Get allowed tools."""
        return [t.name for t in self.tools]

    @root_validator
    def validate_prompt(cls, values: dict) -> dict:
        prompt: BasePromptTemplate = values["prompt"]
        if "agent_scratchpad" not in prompt.input_variables:
            raise ValueError(
                "`agent_scratchpad` should be one of the variables in the prompt, "
                f"got {prompt.input_variables}"
            )
        return values

    @property
    def input_keys(self) -> List[str]:
        """Get input keys. Input refers to user input here."""
        return ["input"]

    @property
    def functions(self) -> List[dict]:
        return [dict(format_tool_to_openai_function(t)) for t in self.tools]

    def plan(
        self,
        intermediate_steps: List[Tuple[AgentAction, str]],
        callbacks: Callbacks = None,
        with_functions: bool = True,
        **kwargs: Any,
    ) -> Union[AgentAction, AgentFinish]:
        """Given input, decided what to do.

        Args:
            intermediate_steps: Steps the LLM has taken to date, along with observations
            **kwargs: User inputs.

        Returns:
            Action specifying what tool to use.
        """
        agent_scratchpad = format_to_openai_function_messages(intermediate_steps)
        selected_inputs = {
            k: kwargs[k] for k in self.prompt.input_variables if k != "agent_scratchpad"
        }
        full_inputs = dict(**selected_inputs, agent_scratchpad=agent_scratchpad)
        prompt = self.prompt.format_prompt(**full_inputs)
        messages = prompt.to_messages()
        if with_functions:
            predicted_message = self.llm.predict_messages(
                messages,
                functions=self.functions,
                callbacks=callbacks,
            )
        else:
            predicted_message = self.llm.predict_messages(
                messages,
                callbacks=callbacks,
            )
        agent_decision = OpenAIFunctionsAgentOutputParser._parse_ai_message(
            predicted_message
        )
        return agent_decision

    async def aplan(
        self,
        intermediate_steps: List[Tuple[AgentAction, str]],
        callbacks: Callbacks = None,
        **kwargs: Any,
    ) -> Union[AgentAction, AgentFinish]:
        """Given input, decided what to do.

        Args:
            intermediate_steps: Steps the LLM has taken to date,
                along with observations
            **kwargs: User inputs.

        Returns:
            Action specifying what tool to use.
        """
        agent_scratchpad = format_to_openai_function_messages(intermediate_steps)
        selected_inputs = {
            k: kwargs[k] for k in self.prompt.input_variables if k != "agent_scratchpad"
        }
        full_inputs = dict(**selected_inputs, agent_scratchpad=agent_scratchpad)
        prompt = self.prompt.format_prompt(**full_inputs)
        messages = prompt.to_messages()
        predicted_message = await self.llm.apredict_messages(
            messages, functions=self.functions, callbacks=callbacks
        )
        agent_decision = OpenAIFunctionsAgentOutputParser._parse_ai_message(
            predicted_message
        )
        return agent_decision

    def return_stopped_response(
        self,
        early_stopping_method: str,
        intermediate_steps: List[Tuple[AgentAction, str]],
        **kwargs: Any,
    ) -> AgentFinish:
        """Return response when agent has been stopped due to max iterations."""
        if early_stopping_method == "force":
            # `force` just returns a constant string
            return AgentFinish(
                {"output": "Agent stopped due to iteration limit or time limit."}, ""
            )
        elif early_stopping_method == "generate":
            # Generate does one final forward pass
            agent_decision = self.plan(
                intermediate_steps, with_functions=False, **kwargs
            )
            if isinstance(agent_decision, AgentFinish):
                return agent_decision
            else:
                raise ValueError(
                    f"got AgentAction with no functions provided: {agent_decision}"
                )
        else:
            raise ValueError(
                "early_stopping_method should be one of `force` or `generate`, "
                f"got {early_stopping_method}"
            )

    @classmethod
    def create_prompt(
        cls,
        system_message: Optional[SystemMessage] = SystemMessage(
            content="You are a helpful AI assistant."
        ),
        extra_prompt_messages: Optional[List[BaseMessagePromptTemplate]] = None,
    ) -> BasePromptTemplate:
        """Create prompt for this agent.

        Args:
            system_message: Message to use as the system message that will be the
                first in the prompt.
            extra_prompt_messages: Prompt messages that will be placed between the
                system message and the new human input.

        Returns:
            A prompt template to pass into this agent.
        """
        _prompts = extra_prompt_messages or []
        messages: List[Union[BaseMessagePromptTemplate, BaseMessage]]
        if system_message:
            messages = [system_message]
        else:
            messages = []

        messages.extend(
            [
                *_prompts,
                HumanMessagePromptTemplate.from_template("{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )
        return ChatPromptTemplate(messages=messages)

    @classmethod
    def from_llm_and_tools(
        cls,
        llm: BaseLanguageModel,
        tools: Sequence[BaseTool],
        callback_manager: Optional[BaseCallbackManager] = None,
        extra_prompt_messages: Optional[List[BaseMessagePromptTemplate]] = None,
        system_message: Optional[SystemMessage] = SystemMessage(
            content="You are a helpful AI assistant."
        ),
        **kwargs: Any,
    ) -> BaseSingleActionAgent:
        """Construct an agent from an LLM and tools."""
        prompt = cls.create_prompt(
            extra_prompt_messages=extra_prompt_messages,
            system_message=system_message,
        )
        return cls(
            llm=llm,
            prompt=prompt,
            tools=tools,
            callback_manager=callback_manager,
            **kwargs,
        )


def create_default_prompt(
    system_message: Optional[str] = "You are a helpful AI assistant.",
    include_chat_history: bool = False,
):
    """Create default prompt for OpenAI Functions agent.

    Args:
        system_message: String to use as the system message,
            defaults to "You are a helpful AI assistant."
        include_chat_history: Whether to include a variable for chat history or not.
            If yes, this will require an input of `chat_history`.

    Returns:
        A prompt template. Requires two or three variables:
        - `input`: this is the user input
        - `agent_scratchpad`: this is the history of steps the agent has taken so far.
            This gets populated by the AgentExecutor.
        - `chat_history`: This is optionally required as input, if
            `include_chat_history` is set to True. It should be a list of LangChain
            Message objects.
    """
    messages: List[Union[BaseMessagePromptTemplate, BaseMessage]]
    messages = [SystemMessage(content=system_message)]
    if include_chat_history:
        messages.append(MessagesPlaceholder(variable_name="chat_history"))
    messages.extend(
        [
            HumanMessagePromptTemplate.from_template("{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    return ChatPromptTemplate(messages=messages)


def create_openai_functions_agent(
    llm: BaseLanguageModel, tools: Sequence[BaseTool], prompt: ChatPromptTemplate
):
    """Create an agent that uses OpenAI function calling.

    Examples:

        Creating an agent with no memory

        .. code-block:: python

            from langchain.agents.openai_functions_agent import (
                create_openai_functions_agent,
                create_default_prompt
            )
            from langchain.chat_models import ChatOpenAI

            prompt = create_default_prompt()
            model = ChatOpenAI()
            tools = ...

            agent = create_openai_functions_agent(model, tools, prompt)

            agent.invoke({"input": "hi"})

        Creating an agent with memory

        .. code-block:: python

            from langchain.agents.openai_functions_agent import (
                create_openai_functions_agent,
                create_default_prompt
            )
            from langchain.chat_models import ChatOpenAI

            prompt = create_default_prompt(include_chat_history=True)
            model = ChatOpenAI()
            tools = ...

            agent = create_openai_functions_agent(model, tools, prompt)

            # Call for the first time with no memory
            agent.invoke({"input": "hi", "chat_history"=[]})

            # Call for the second time and pass in chat messages
            from langchain_core.messages import HumanMessage, AIMessage
            messages = [HumanMessage(content="hi"), AIMessage(content="hi")]
            agent.invoke({"input": "hi", "chat_history"=messages})

    Args:
        llm: LLM to use as the agent. Should work with OpenAI function calling,
            so either be an OpenAI model that supports that or a wrapper of
            a different model that adds in equivalent support.
        tools: Tools this agent has access to.
        prompt: The prompt to use, must have an input key of `agent_scratchpad`.

    Returns:
        A runnable sequence representing an agent. It takes as input all the same input
        variables as the prompt passed in does. It returns as output either an
        AgentAction or AgentFinish.

    """
    if "agent_scratchpad" not in prompt.input_variables:
        raise ValueError(
            "Prompt must have input variable `agent_scratchpad`, but wasn't found. "
            f"Found {prompt.input_variables} instead."
        )
    llm_with_tools = llm.bind(
        functions=[format_tool_to_openai_function(t) for t in tools]
    )
    agent = (
        RunnablePassthrough.assign(
            agent_scratchpad=lambda x: format_to_openai_function_messages(
                x["intermediate_steps"]
            )
        )
        | prompt
        | llm_with_tools
        | OpenAIFunctionsAgentOutputParser()
    )
    return agent
