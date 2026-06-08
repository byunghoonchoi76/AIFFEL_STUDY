"""
RAG Pipeline - 기본 + 고급 기법
Day 4 학습 내용 기반 구현
"""
from typing import Optional
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser


# ============================================================
# 기본 RAG 파이프라인
# ============================================================

class BasicRAG:
    """
    기본 RAG 파이프라인

    Example:
        >>> rag = BasicRAG()
        >>> rag.index_documents("./docs")
        >>> answer = rag.query("질문을 입력하세요")
        >>> print(answer)
    """

    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        k: int = 5,
    ):
        self.embedding = OpenAIEmbeddings()
        self.llm = ChatOpenAI(model=model_name, temperature=0)
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self.k = k
        self.vectordb = None
        self.chain = None

    def index_documents(self, path: str, glob: str = "**/*.txt"):
        """문서를 로드하고 벡터 DB에 인덱싱"""
        loader = DirectoryLoader(path, glob=glob, loader_cls=TextLoader)
        docs = loader.load()
        chunks = self.splitter.split_documents(docs)
        self.vectordb = Chroma.from_documents(chunks, self.embedding)
        self._build_chain()
        print(f"✅ {len(chunks)}개 청크 인덱싱 완료")

    def _build_chain(self):
        retriever = self.vectordb.as_retriever(search_kwargs={"k": self.k})
        prompt = ChatPromptTemplate.from_template(
            """다음 컨텍스트를 바탕으로 질문에 답하세요.
컨텍스트:
{context}

질문: {question}
답변:"""
        )
        self.chain = (
            {"context": retriever, "question": RunnablePassthrough()}
            | prompt
            | self.llm
            | StrOutputParser()
        )

    def query(self, question: str) -> str:
        if self.chain is None:
            raise RuntimeError("index_documents()를 먼저 호출하세요.")
        return self.chain.invoke(question)


# ============================================================
# 고급 RAG 기법
# ============================================================

class AdvancedRAG(BasicRAG):
    """
    고급 RAG 기법 모음 (HyDE, Multi-Query, Hybrid, Reranking)
    """

    def hyde_query(self, question: str) -> str:
        """
        HyDE: 가상 답변을 생성한 후 임베딩으로 검색
        질문과 답변의 임베딩 공간 차이를 극복
        """
        hyde_prompt = ChatPromptTemplate.from_template(
            "다음 질문에 대한 가상의 상세한 답변을 작성하세요:\n{question}"
        )
        hypothetical = (hyde_prompt | self.llm | StrOutputParser()).invoke(
            {"question": question}
        )
        retriever = self.vectordb.as_retriever(search_kwargs={"k": self.k})
        docs = retriever.invoke(hypothetical)
        context = "\n\n".join(d.page_content for d in docs)

        answer_prompt = ChatPromptTemplate.from_template(
            "컨텍스트:\n{context}\n\n질문: {question}\n답변:"
        )
        return (answer_prompt | self.llm | StrOutputParser()).invoke(
            {"context": context, "question": question}
        )

    def multi_query(self, question: str) -> str:
        """
        Multi-Query: 질문을 여러 방식으로 변환하여 다양한 문서 검색
        """
        from langchain.retrievers.multi_query import MultiQueryRetriever

        retriever = MultiQueryRetriever.from_llm(
            retriever=self.vectordb.as_retriever(search_kwargs={"k": 3}),
            llm=self.llm,
        )
        prompt = ChatPromptTemplate.from_template(
            "컨텍스트:\n{context}\n\n질문: {question}\n답변:"
        )
        chain = (
            {"context": retriever, "question": RunnablePassthrough()}
            | prompt
            | self.llm
            | StrOutputParser()
        )
        return chain.invoke(question)

    def hybrid_query(self, question: str, docs: list) -> str:
        """
        Hybrid Search: Dense(임베딩) + Sparse(BM25) 결합
        """
        from langchain_community.retrievers import BM25Retriever
        from langchain.retrievers import EnsembleRetriever

        bm25 = BM25Retriever.from_documents(docs)
        bm25.k = self.k
        dense = self.vectordb.as_retriever(search_kwargs={"k": self.k})

        ensemble = EnsembleRetriever(
            retrievers=[bm25, dense],
            weights=[0.5, 0.5],
        )
        prompt = ChatPromptTemplate.from_template(
            "컨텍스트:\n{context}\n\n질문: {question}\n답변:"
        )
        chain = (
            {"context": ensemble, "question": RunnablePassthrough()}
            | prompt
            | self.llm
            | StrOutputParser()
        )
        return chain.invoke(question)
