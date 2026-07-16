# Workflow Architecture

## Pipeline 1: Google Calendar

1. `google calender`
    
2. `Extractor`
    
3. `CSV 1`
    
4. `Cleaner`
    
5. `CSV 1 clean`
    
6. `classifier`
    
7. `editor (Me)`
    
8. `CSV 1` $\rightarrow$ (Branch: `Learn from This`)
    

## Pipeline 2: Notes

1. `Notes`
    
2. `Reader`
    
3. `Tasks`
    
4. `classifier`
    
5. `Editor`
    
6. `CSV 2` $\rightarrow$ (Branch: `Learn from this`)
    

## Merging and Output

1. Merge `CSV 1` and `CSV 2` $\rightarrow$ `Merged CSV`
    
2. `Rearragement of events to fit the tasks`
    
3. `editor`
    
4. `Final CSV` $\rightarrow$ (Branch: `Learn from this`)
    
5. `googl calendur`
    

## File Naming / Legend

- `CSV 1` $\rightarrow$ `@Source_CSV`
    
- `CSV 1_cleaned` $\rightarrow$ `CSV_clean`
    
- `CSV 1_final` $\rightarrow$ `CSV_Final`
    
- `CSV 2` $\rightarrow$ `Classified_CSV`