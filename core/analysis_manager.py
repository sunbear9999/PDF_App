import fitz
import json

class AnalysisManager:
    def __init__(self, llm_manager):
        self.llm = llm_manager

    def run_analysis_stream(self, doc_path, template, model, progress_callback):
        try:
            doc = fitz.open(doc_path)
        except Exception as e:
            yield {"error": f"Failed to read PDF: {e}"}
            return

        chunks = []
        current_chunk_text = ""
        start_page = 1
        
        # 1. Extract Text & Track Pages (Document MUST stay open here!)
        try:
            for page_num, page in enumerate(doc):
                current_chunk_text += page.get_text("text") + "\n"
                
                # If the chunk gets large enough, or it's the final page, lock it in
                if len(current_chunk_text.split()) >= 2000 or page_num == len(doc) - 1:
                    end_page = page_num + 1
                    page_range_str = f"p.{start_page}" if start_page == end_page else f"p.{start_page}-{end_page}"
                    
                    chunks.append({
                        "page_range": page_range_str,
                        "text": current_chunk_text
                    })
                    
                    current_chunk_text = ""
                    start_page = page_num + 2 # Reset for next chunk
        finally:
            # Safely close the document ONLY AFTER we are done reading pages
            doc.close()

        # 2. Process Chunks with the LLM
        for idx, chunk_data in enumerate(chunks):
            chunk_text = chunk_data["text"]
            page_range = chunk_data["page_range"]
            
            progress_callback(f"Analyzing Section {idx + 1} ({page_range})...")
            
            # Hardcoded prompt to bypass cache and guarantee schema adherence
            system_prompt = (
                "You are an expert document analysis engine. Your task is to analyze ONLY the current section of text.\n"
                f"INSTRUCTIONS: {template.get('instructions', 'Extract key insights.')}\n\n"
                "CRITICAL INSTRUCTIONS:\n"
                "1. Extract insights strictly from the new text chunk provided.\n"
                "2. Output ONLY valid, raw JSON matching the exact schema provided.\n"
                "3. You MUST use the EXACT keys shown in the schema below. Do not invent new keys or wrap the output in a parent 'Analysis' key.\n"
                "4. Output the raw JSON object directly without markdown wrapping or ```json blocks.\n\n"
                f"REQUIRED JSON SCHEMA:\n{template.get('schema', '{}')}"
            )
            
            user_prompt = f"--- TEXT TO ANALYZE (Pages: {page_range}) ---\n{chunk_text}"

            raw_response = self.llm.query(
                question=user_prompt,
                selected_model=model,
                rag_enabled=False,
                use_agents=False,
                custom_system_prompt=system_prompt,
                json_mode=True,       
                num_predict=800  
            )

            clean_json_str = raw_response.strip()
            if clean_json_str.startswith("```json"):
                clean_json_str = clean_json_str[7:]
            if clean_json_str.startswith("```"):
                clean_json_str = clean_json_str[3:]
            clean_json_str = clean_json_str.strip("` \n")

            try:
                parsed_data = json.loads(clean_json_str)
                # We yield the page_range here so the UI can capture it!
                yield {"chunk_index": idx, "data": parsed_data, "page_range": page_range}
            except json.JSONDecodeError:
                print(f"[System] Chunk {idx+1} failed JSON parsing. Skipping to maintain map integrity.")
                pass