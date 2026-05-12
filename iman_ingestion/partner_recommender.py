import pandas as pd
import numpy as np
from numpy.linalg import norm
import json

### DATA LOADING ###
# def load_data():
        
### AUX FUNCTION ###

def cosine_similarity(v1, v2):
    return np.dot(v1, v2) / (norm(v1) * norm(v2))

### ALGORITHM ###

def recommend_partners(target_embedding, coordinator_search, projects_df, orgs_df, relations_df, top_k_search=50, top_n_results=5):
    """
    target_embedding: target call embeddings
    coordinator_search: True if searching for coordinators
    """
    
    # Embedding search
    projects_df['sim_score'] = projects_df['embedding'].apply(lambda emb: cosine_similarity(target_embedding, emb))
    top_projects = projects_df.nlargest(top_k_search, 'sim_score')[['projectID', 'title', 'sim_score']]
    
    # Merge top projects with relations and organizations
    merged_df = pd.merge(top_projects, relations_df, on='projectID')
    merged_df = pd.merge(merged_df, orgs_df, on='organisationID')
    
    recommendations = []
    
    # Compute scores by org
    grouped = merged_df.groupby('organisationID')
    
    for org_id, group in grouped:
        org_name = group['name'].iloc[0]
        
        # A. Score Base
        s_exp = sum(group['sim_score']**2)
        num_projects = len(group)
        avg_sim = group['sim_score'].mean()
        
        # B. Role
        total_roles = len(group['role'])
        pct_participant = (group['role'] != 'coordinator').sum() / total_roles
        pct_coordinator = (group['role'] == 'coordinator').sum() / total_roles
        
        if coordinator_search:
            # Searching for coordinator
            m_role = 1.0 + (0.2 * pct_coordinator)
            role_reason = f"{pct_coordinator*100:.0f}% de veces como coordinador."
        else:
            m_role = 1.0
            
        # C. Relation
        avg_interest = group['INTEREST'].mean()
        
        if pd.isna(avg_interest):
            m_int = 1.0 # Neutral 
            trust_reason = "Socio nuevo (sin historial de interés)."
        else:
            m_int = 0.5 + (avg_interest / 5.0) 
            trust_reason = f"Nota interna de confianza: {avg_interest:.1f}/5."
            
        # D. Score Final
        final_score = s_exp * m_role * m_int
        
        # Save
        recommendations.append({
            'organisationID': org_id,
            'name': org_name,
            'score': round(final_score, 2),
            'explicacion': {
                '1_dominio_tecnico': f"{num_projects} proyectos afines encontrados (Similitud media: {avg_sim:.2f}).",
                '2_afinidad_rol': role_reason,
                '3_confianza': trust_reason
            }
        })
        
    # Order and get top K
    recommendations = sorted(recommendations, key=lambda x: x['score'], reverse=True)[:top_n_results]
    
    return recommendations


# if __name__ == "__main__":
    # load_data()
    
    # top_partners = recommend_partners(
    #     target_embedding=,
    #     user_role_intent='coordinator', 
    #     projects_df=df_projects, 
    #     orgs_df=df_orgs, 
    #     relations_df=df_relations,
    #     top_k_search=10, 
    #     top_n_results=3
    # )
    
    # print(json.dumps(top_partners, indent=2, ensure_ascii=False))
    