import os
import logging
import time


def make_path_safe(path):
    return path.replace('\\', '/').replace(' ', '%20')


# Use planner=rule to avoid non-use of unique constraints

def import_type_csvs(corpus_context, type_headers):
    """
    Imports types into corpus from csv files

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.CorpusContext`
        the corpus to import into
    type_headers : list
        a list of type files
    """
    log = logging.getLogger('{}_loading'.format(corpus_context.corpus_name))
    prop_temp = '''{name}: csvLine.{name}'''
    for at, h in type_headers.items():
        path = os.path.join(corpus_context.config.temporary_directory('csv'),
                            '{}_type.csv'.format(at))
        # If on the Docker version, the files live in /site/proj
        if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
            type_path = 'file:///site/proj/{}'.format(make_path_safe(path))
        else:
            type_path = 'file:///{}'.format(make_path_safe(path))

        corpus_context.execute_cypher('CREATE CONSTRAINT ON (node:%s_type) ASSERT node.id IS UNIQUE' % at)

        properties = []
        for x in h:
            properties.append(prop_temp.format(name=x))
        if 'label' in h:
            properties.append('label_insensitive: lower(csvLine.label)')
            corpus_context.execute_cypher('CREATE INDEX ON :%s_type(label_insensitive)' % at)
        for x in h:
            if x != 'id':
                corpus_context.execute_cypher('CREATE INDEX ON :%s_type(%s)' % (at, x))
        if properties:
            type_prop_string = ', '.join(properties)
        else:
            type_prop_string = ''
        type_import_statement = '''CYPHER planner=rule USING PERIODIC COMMIT 2000
LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
MERGE (n:{annotation_type}_type:{corpus_name} {{ {type_property_string} }})
        '''
        kwargs = {'path': type_path, 'annotation_type': at,
                  'type_property_string': type_prop_string,
                  'corpus_name': corpus_context.cypher_safe_name}
        statement = type_import_statement.format(**kwargs)
        log.info('Loading {} types...'.format(at))
        begin = time.time()
        try:
            corpus_context.execute_cypher(statement)
        except:
            raise
            # finally:
            #    with open(path, 'w'):
            #        pass
            # os.remove(path) # FIXME Neo4j 2.3 does not release files

        log.info('Finished loading {} types!'.format(at))
        log.debug('{} type loading took: {} seconds.'.format(at, time.time() - begin))


def import_csvs(corpus_context, data, call_back=None, stop_check=None):
    """
    Loads data from a csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.CorpusContext`
        the corpus to load into
    data : :class:`~polyglotdb.io.helper.DiscourseData`
        the data object
    """
    log = logging.getLogger('{}_loading'.format(corpus_context.corpus_name))
    log.info('Beginning to import data into the graph database...')
    initial_begin = time.time()
    name, annotation_types = data.name, data.annotation_types

    prop_temp = '''{name}: csvLine.{name}'''

    directory = corpus_context.config.temporary_directory('csv')
    speakers = corpus_context.speakers
    annotation_types = data.highest_to_lowest()
    if call_back is not None:
        call_back('Importing data...')
        call_back(0, len(speakers) * len(annotation_types))
        cur = 0
    statements = []

    def unique_function(tx, at):
        tx.run('CREATE CONSTRAINT ON (node:%s) ASSERT node.id IS UNIQUE' % at)

    def prop_index(tx, at, prop):
        tx.run('CREATE INDEX ON :%s(%s)' % (at, prop))

    def label_index(tx, at):
        tx.run('CREATE INDEX ON :%s(label_insensitive)' % at)

    def begin_index(tx, at):
        tx.run('CREATE INDEX ON :%s(begin)' % (at,))

    def end_index(tx, at):
        tx.run('CREATE INDEX ON :%s(end)' % (at,))

    with corpus_context.graph_driver.session() as session:
        for i, s in enumerate(speakers):
            speaker_statements = []
            for at in annotation_types:
                if stop_check is not None and stop_check():
                    return
                if call_back is not None:
                    call_back(cur)
                    cur += 1
                path = os.path.join(directory, '{}_{}.csv'.format(s, at))
                # If on the Docker version, the files live in /site/proj
                if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
                    rel_path = 'file:///site/proj/{}'.format(make_path_safe(path))
                else:
                    rel_path = 'file:///{}'.format(make_path_safe(path))

                session.write_transaction(unique_function, at)

                properties = []

                for x in data[at].token_property_keys:
                    properties.append(prop_temp.format(name=x))
                    session.write_transaction(prop_index, at, x)
                if 'label' in data[at].token_property_keys:
                    properties.append('label_insensitive: lower(csvLine.label)')
                    session.write_transaction(label_index, at)
                st = data[at].supertype
                if properties:
                    token_prop_string = ', ' + ', '.join(properties)
                else:
                    token_prop_string = ''
                node_import_statement = '''USING PERIODIC COMMIT 2000
                LOAD CSV WITH HEADERS FROM '{path}' AS csvLine
                CREATE (t:{annotation_type}:{corpus_name}:speech {{id: csvLine.id, begin: toFloat(csvLine.begin),
                                            end: toFloat(csvLine.end){token_property_string} }})
                '''
                node_kwargs = {'path': rel_path, 'annotation_type': at,
                          'token_property_string': token_prop_string,
                          'corpus_name': corpus_context.cypher_safe_name}
                if st is not None:
                    rel_import_statement = '''USING PERIODIC COMMIT 2000
                    LOAD CSV WITH HEADERS FROM '{path}' AS csvLine
                    MATCH (n:{annotation_type}_type:{corpus_name} {{id: csvLine.type_id}}), (super:{stype}:{corpus_name} {{id: csvLine.{stype}}}),
                    (d:Discourse:{corpus_name} {{name: csvLine.discourse}}),
                    (s:Speaker:{corpus_name} {{name: csvLine.speaker}}),
                    (t:{annotation_type}:{corpus_name}:speech {{id: csvLine.id}})
                    CREATE (t)-[:is_a]->(n),
                        (t)-[:contained_by]->(super),
                        (t)-[:spoken_in]->(d),
                        (t)-[:spoken_by]->(s)
                    WITH t, csvLine
                    MATCH (p:{annotation_type}:{corpus_name}:speech {{id: csvLine.previous_id}})
                        CREATE (p)-[:precedes]->(t)
                    '''
                    rel_kwargs = {'path': rel_path, 'annotation_type': at,
                              'corpus_name': corpus_context.cypher_safe_name,
                              'stype': st}
                else:

                    rel_import_statement = '''USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM '{path}' AS csvLine
            MATCH (n:{annotation_type}_type:{corpus_name} {{id: csvLine.type_id}}),
            (d:Discourse:{corpus_name} {{name: csvLine.discourse}}),
            (s:Speaker:{corpus_name} {{ name: csvLine.speaker}}),
                    (t:{annotation_type}:{corpus_name}:speech {{id: csvLine.id}})
            CREATE (t)-[:is_a]->(n),
                    (t)-[:spoken_in]->(d),
                    (t)-[:spoken_by]->(s)
                WITH t, csvLine
                MATCH (p:{annotation_type}:{corpus_name}:speech {{id: csvLine.previous_id}})
                    CREATE (p)-[:precedes]->(t)
            '''
                    rel_kwargs = {'path': rel_path, 'annotation_type': at,
                              'corpus_name': corpus_context.cypher_safe_name}
                node_statement = node_import_statement.format(**node_kwargs)
                rel_statement = rel_import_statement.format(**rel_kwargs)
                speaker_statements.append((node_statement, rel_statement))
                begin = time.time()
                session.write_transaction(begin_index, at)
                session.write_transaction(end_index, at)
            statements.append(speaker_statements)

    for i, speaker_statements in enumerate(statements):
        if call_back is not None:
            call_back('Importing data for speaker {} of {} ({})...'.format(i, len(speakers), speakers[i]))
        for s in speaker_statements:
            log.info('Loading {} relationships...'.format(at))
            corpus_context.execute_cypher(s[0])
            corpus_context.execute_cypher(s[1])
            log.info('Finished loading {} relationships!'.format(at))
            log.debug('{} relationships loading took: {} seconds.'.format(at, time.time() - begin))

    log.info('Finished importing {} into the graph database!'.format(data.name))
    log.debug('Graph importing took: {} seconds'.format(time.time() - initial_begin))

    for sp in corpus_context.speakers:
        for k, v in data.hierarchy.subannotations.items():
            for s in v:
                path = os.path.join(directory, '{}_{}_{}.csv'.format(sp, k, s))
                corpus_context.execute_cypher('CREATE CONSTRAINT ON (node:%s) ASSERT node.id IS UNIQUE' % s)
                # If on the Docker version, the files live in /site/proj
                if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
                    sub_path = 'file:///site/proj/{}'.format(make_path_safe(path))
                else:
                    sub_path = 'file:///{}'.format(make_path_safe(path))

                rel_import_statement = '''USING PERIODIC COMMIT 1000
    LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
    MATCH (n:{annotation_type} {{id: csvLine.annotation_id}})
    CREATE (t:{subannotation_type}:{corpus_name}:speech {{id: csvLine.id, begin: toFloat(csvLine.begin),
                                end: toFloat(csvLine.end), label: CASE csvLine.label WHEN NULL THEN '' ELSE csvLine.label END  }})
    CREATE (t)-[:annotates]->(n)'''
                kwargs = {'path': sub_path, 'annotation_type': k,
                          'subannotation_type': s,
                          'corpus_name': corpus_context.cypher_safe_name}
                statement = rel_import_statement.format(**kwargs)
                try:
                    corpus_context.execute_cypher(statement)
                except:
                    raise
                    # finally:
                    # with open(path, 'w'):
                    #    pass
                    # os.remove(path) # FIXME Neo4j 2.3 does not release files


def import_lexicon_csvs(corpus_context, typed_data, case_sensitive=False):
    """
    Import a lexicon from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.CorpusContext`
        the corpus to load into
    typed_data : dict
        the data
    case_sensitive : boolean
        defaults to false

    """
    string_set_template = 'n.{name} = csvLine.{name}'
    float_set_template = 'n.{name} = toFloat(csvLine.{name})'
    int_set_template = 'n.{name} = toInt(csvLine.{name})'
    bool_set_template = '''n.{name} = (CASE WHEN csvLine.{name} = 'False' THEN false ELSE true END)'''
    properties = []
    for h, v in typed_data.items():
        if v == int:
            template = int_set_template
        elif v == bool:
            template = bool_set_template
        elif v == float:
            template = float_set_template
        else:
            template = string_set_template
        properties.append(template.format(name=h))
    properties = ',\n'.join(properties)
    directory = corpus_context.config.temporary_directory('csv')
    path = os.path.join(directory, 'lexicon_import.csv')
    # If on the Docker version, the files live in /site/proj
    if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
        lex_path = 'file:///site/proj/{}'.format(make_path_safe(path))
    else:
        lex_path = 'file:///{}'.format(make_path_safe(path))
    if case_sensitive:
        import_statement = '''CYPHER planner=rule USING PERIODIC COMMIT 3000
    LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
    with csvLine
    MATCH (n:{word_type}_type:{corpus_name}) where n.label = csvLine.label
    SET {new_properties}'''
    else:
        import_statement = '''CYPHER planner=rule USING PERIODIC COMMIT 3000
    LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
    MATCH (n:{word_type}_type:{corpus_name}) where n.label_insensitive = csvLine.label
    SET {new_properties}'''

    statement = import_statement.format(path=lex_path,
                                        corpus_name=corpus_context.cypher_safe_name,
                                        word_type=corpus_context.word_name,
                                        new_properties=properties)
    corpus_context.execute_cypher(statement)
    for h, v in typed_data.items():
        corpus_context.execute_cypher('CREATE INDEX ON :%s(%s)' % (corpus_context.word_name, h))
        # os.remove(path) # FIXME Neo4j 2.3 does not release files


def import_feature_csvs(corpus_context, typed_data):
    """
    Import features from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.CorpusContext`
        the corpus to load into
    typed_data : dict
        the data
    """
    string_set_template = 'n.{name} = csvLine.{name}'
    float_set_template = 'n.{name} = toFloat(csvLine.{name})'
    int_set_template = 'n.{name} = toInt(csvLine.{name})'
    bool_set_template = '''n.{name} = (CASE WHEN csvLine.{name} = 'False' THEN false ELSE true END)'''
    properties = []
    for h, v in typed_data.items():
        if v == int:
            template = int_set_template
        elif v == bool:
            template = bool_set_template
        elif v == float:
            template = float_set_template
        else:
            template = string_set_template
        properties.append(template.format(name=h))
    properties = ',\n'.join(properties)
    directory = corpus_context.config.temporary_directory('csv')
    path = os.path.join(directory, 'feature_import.csv')

    # If on the Docker version, the files live in /site/proj
    if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
        feat_path = 'file:///site/proj/{}'.format(make_path_safe(path))
    else:
        feat_path = 'file:///{}'.format(make_path_safe(path))
    
    import_statement = '''CYPHER planner=rule
    LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
    MATCH (n:{phone_type}_type:{corpus_name}) where n.label = csvLine.label
    SET {new_properties}'''

    statement = import_statement.format(path=feat_path,
                                        corpus_name=corpus_context.cypher_safe_name,
                                        phone_type=corpus_context.phone_name,
                                        new_properties=properties)
    corpus_context.execute_cypher(statement)
    for h, v in typed_data.items():
        corpus_context.execute_cypher('CREATE INDEX ON :%s(%s)' % (corpus_context.phone_name, h))
        # os.remove(path) # FIXME Neo4j 2.3 does not release files


def import_syllable_enrichment_csvs(corpus_context, typed_data):
    """
    Import syllable features from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.syllabic.SyllabicContext`
        the corpus to load into
    typed_data : dict
        the data
    """
    string_set_template = 'n.{name} = csvLine.{name}'
    float_set_template = 'n.{name} = toFloat(csvLine.{name})'
    int_set_template = 'n.{name} = toInt(csvLine.{name})'
    bool_set_template = '''n.{name} = (CASE WHEN csvLine.{name} = 'False' THEN false ELSE true END)'''
    properties = []
    for h, v in typed_data.items():
        if v == int:
            template = int_set_template
        elif v == bool:
            template = bool_set_template
        elif v == float:
            template = float_set_template
        else:
            template = string_set_template
        properties.append(template.format(name=h))
    properties = ',\n'.join(properties)
    directory = corpus_context.config.temporary_directory('csv')
    path = os.path.join(directory, 'syllable_import.csv')

    # If on the Docker version, the files live in /site/proj
    if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
        syl_path = 'file:///site/proj/{}'.format(make_path_safe(path))
    else:
        syl_path = 'file:///{}'.format(make_path_safe(path))
    
    import_statement = '''CYPHER planner=rule
    LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
    MATCH (n:syllable_type:{corpus_name}) where n.label = csvLine.label
    SET {new_properties}'''

    statement = import_statement.format(path=syl_path,
                                        corpus_name=corpus_context.cypher_safe_name,
                                        phone_type="syllable",
                                        new_properties=properties)
    corpus_context.execute_cypher(statement)
    for h, v in typed_data.items():
        corpus_context.execute_cypher('CREATE INDEX ON :%s(%s)' % ("syllable", h))


def import_utterance_enrichment_csvs(corpus_context, typed_data):
    """
    Import syllable features from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.CorpusContext`
        the corpus to load into
    typed_data : dict
        the data
    """
    string_set_template = 'n.{name} = csvLine.{name}'
    float_set_template = 'n.{name} = toFloat(csvLine.{name})'
    int_set_template = 'n.{name} = toInt(csvLine.{name})'
    bool_set_template = '''n.{name} = (CASE WHEN csvLine.{name} = 'False' THEN false ELSE true END)'''
    properties = []
    for h, v in typed_data.items():
        if v == int:
            template = int_set_template
        elif v == bool:
            template = bool_set_template
        elif v == float:
            template = float_set_template
        else:
            template = string_set_template
        properties.append(template.format(name=h))
    properties = ',\n'.join(properties)
    directory = corpus_context.config.temporary_directory('csv')
    path = os.path.join(directory, 'utterance_enrichment.csv')

    # If on the Docker version, the files live in /site/proj
    if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
        utt_path = 'file:///site/proj/{}'.format(make_path_safe(path))
    else:
        utt_path = 'file:///{}'.format(make_path_safe(path))
    
    import_statement = '''CYPHER planner=rule
    LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
    MATCH (n:utterance:{corpus_name}) where n.id = csvLine.id
    SET {new_properties}'''

    statement = import_statement.format(path=utt_path,
                                        corpus_name=corpus_context.cypher_safe_name,
                                        phone_type="syllable",
                                        new_properties=properties)
    corpus_context.execute_cypher(statement)
    for h, v in typed_data.items():
        corpus_context.execute_cypher('CREATE INDEX ON :%s(%s)' % ("utterance", h))


def import_speaker_csvs(corpus_context, typed_data):
    """
    Import a speaker from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.CorpusContext`
        the corpus to load into
    typed_data : dict
        the data
    """
    string_set_template = 'n.{name} = csvLine.{name}'
    float_set_template = 'n.{name} = toFloat(csvLine.{name})'
    int_set_template = 'n.{name} = toInt(csvLine.{name})'
    bool_set_template = '''n.{name} = (CASE WHEN csvLine.{name} = 'False' THEN false ELSE true END)'''
    properties = []
    for h, v in typed_data.items():
        if v == int:
            template = int_set_template
        elif v == bool:
            template = bool_set_template
        elif v == float:
            template = float_set_template
        else:
            template = string_set_template
        properties.append(template.format(name=h))
    properties = ',\n'.join(properties)
    directory = corpus_context.config.temporary_directory('csv')
    path = os.path.join(directory, 'speaker_import.csv')

    # If on the Docker version, the files live in /site/proj
    if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
        feat_path = 'file:///site/proj/{}'.format(make_path_safe(path))
    else:
        feat_path = 'file:///{}'.format(make_path_safe(path))
    
    import_statement = '''CYPHER planner=rule
    LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
    MATCH (n:Speaker:{corpus_name}) where n.name = toString(csvLine.name)
    SET {new_properties}'''

    statement = import_statement.format(path=feat_path,
                                        corpus_name=corpus_context.cypher_safe_name,
                                        new_properties=properties)
    corpus_context.execute_cypher(statement)
    for h, v in typed_data.items():
        corpus_context.execute_cypher('CREATE INDEX ON :Speaker(%s)' % h)
        # os.remove(path) # FIXME Neo4j 2.3 does not release files


def import_discourse_csvs(corpus_context, typed_data):
    """
    Import a discourse from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.CorpusContext`
        the corpus to load into
    typed_data : dict
        the data
    """
    string_set_template = 'n.{name} = csvLine.{name}'
    float_set_template = 'n.{name} = toFloat(csvLine.{name})'
    int_set_template = 'n.{name} = toInt(csvLine.{name})'
    bool_set_template = '''n.{name} = (CASE WHEN csvLine.{name} = 'False' THEN false ELSE true END)'''
    properties = []
    for h, v in typed_data.items():
        if v == int:
            template = int_set_template
        elif v == bool:
            template = bool_set_template
        elif v == float:
            template = float_set_template
        else:
            template = string_set_template
        properties.append(template.format(name=h))
    properties = ',\n'.join(properties)
    directory = corpus_context.config.temporary_directory('csv')
    path = os.path.join(directory, 'discourse_import.csv')

    # If on the Docker version, the files live in /site/proj
    if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
        feat_path = 'file:///site/proj/{}'.format(make_path_safe(path))
    else:
        feat_path = 'file:///{}'.format(make_path_safe(path))
    
    import_statement = '''CYPHER planner=rule
    LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
    MATCH (n:Discourse:{corpus_name}) where n.name = toString(csvLine.name)
    SET {new_properties}'''

    statement = import_statement.format(path=feat_path,
                                        corpus_name=corpus_context.cypher_safe_name,
                                        new_properties=properties)
    corpus_context.execute_cypher(statement)
    for h, v in typed_data.items():
        corpus_context.execute_cypher('CREATE INDEX ON :Discourse(%s)' % h)
        # os.remove(path) # FIXME Neo4j 2.3 does not release files


def import_utterance_csv(corpus_context, call_back=None, stop_check=None):
    """
    Import an utterance from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.CorpusContext`
        the corpus to load into
    discourse : str
        the discourse the utterance is in
    """
    speakers = corpus_context.speakers
    if call_back is not None:
        call_back('Importing data...')
        call_back(0, len(speakers))
    corpus_context.execute_cypher('CREATE CONSTRAINT ON (node:utterance) ASSERT node.id IS UNIQUE')
    for i, s in enumerate(speakers):
        if stop_check is not None and stop_check():
            return
        if call_back is not None:
            call_back('Importing data for speaker {} of {} ({})...'.format(i, len(speakers), s))
            call_back(i)

        path = os.path.join(corpus_context.config.temporary_directory('csv'), '{}_utterance.csv'.format(s))
        
        # If on the Docker version, the files live in /site/proj
        if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
            csv_path = 'file:///site/proj/{}'.format(make_path_safe(path))
        else:
            csv_path = 'file:///{}'.format(make_path_safe(path))

        node_statement = '''
        USING PERIODIC COMMIT 1000
                LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
                MATCH (begin:{word_type}:{corpus}:speech {{id: csvLine.begin_word_id}}),
                (end:{word_type}:{corpus}:speech {{id: csvLine.end_word_id}})
                WITH csvLine, begin, end
                CREATE (utt:utterance:{corpus}:speech {{id: csvLine.id, begin: begin.begin, end: end.end}})-[:is_a]->(u_type:utterance_type:{corpus})
        '''

        node_statement = node_statement.format(path=csv_path,
                                     corpus=corpus_context.cypher_safe_name,
                                     word_type=corpus_context.word_name)
        corpus_context.execute_cypher(node_statement)
        rel_statement = '''USING PERIODIC COMMIT 1000
                LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
                MATCH (d:Discourse:{corpus})<-[:spoken_in]-(begin:{word_type}:{corpus}:speech {{id: csvLine.begin_word_id}})-[:spoken_by]->(s:Speaker:{corpus}),
                (utt:utterance:{corpus}:speech {{id: csvLine.id}})
                CREATE
                    (d)<-[:spoken_in]-(utt),
                    (s)<-[:spoken_by]-(utt)
                WITH utt,  csvLine

                MATCH (prev:utterance {{id:csvLine.prev_id}})
                CREATE (prev)-[:precedes]->(utt)
        '''
        rel_statement = rel_statement.format(path=csv_path,
                                     corpus=corpus_context.cypher_safe_name,
                                     word_type=corpus_context.word_name)
        corpus_context.execute_cypher(rel_statement)

        word_statement = '''USING PERIODIC COMMIT 1000
                LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
                MATCH (begin:{word_type}:{corpus}:speech {{id: csvLine.begin_word_id}}),
                (utt:utterance:{corpus}:speech {{id: csvLine.id}}),
                (end:{word_type}:{corpus}:speech {{id: csvLine.end_word_id}}),
                path = shortestPath((begin)-[:precedes*0..]->(end))
                WITH utt, nodes(path) as words
                UNWIND words as w
                CREATE (w)-[:contained_by]->(utt)
        '''
        word_statement = word_statement.format(path=csv_path,
                                     corpus=corpus_context.cypher_safe_name,
                                     word_type=corpus_context.word_name)
        corpus_context.execute_cypher(word_statement)
        # os.remove(path) # FIXME Neo4j 2.3 does not release files


def import_syllable_csv(corpus_context, call_back=None, stop_check=None):
    """
    Import a syllable from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.syllabic.SyllabicContext`
        the corpus to load into
    split_name : str
        the identifier of the file
    """

    speakers = corpus_context.speakers
    if call_back is not None:
        call_back('Importing syllables...')
        call_back(0, len(speakers))
    corpus_context.execute_cypher('CREATE CONSTRAINT ON (node:syllable) ASSERT node.id IS UNIQUE')
    corpus_context.execute_cypher('CREATE CONSTRAINT ON (node:syllable_type) ASSERT node.id IS UNIQUE')
    corpus_context.execute_cypher('CREATE INDEX ON :syllable(begin)')
    corpus_context.execute_cypher('CREATE INDEX ON :syllable(end)')
    corpus_context.execute_cypher('CREATE INDEX ON :syllable(label)')
    corpus_context.execute_cypher('CREATE INDEX ON :syllable_type(label)')
    for i, s in enumerate(speakers):
        if stop_check is not None and stop_check():
            return
        if call_back is not None:
            call_back('Importing syllables for speaker {} of {} ({})...'.format(i, len(speakers), s))
            call_back(i)
        discourses = corpus_context.get_discourses_of_speaker(s)
        for d in discourses:
            path = os.path.join(corpus_context.config.temporary_directory('csv'),
                                '{}_{}_syllable.csv'.format(s, d))
            print('syl', s, d, path)
            # If on the Docker version, the files live in /site/proj
            if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
                csv_path = 'file:///site/proj/{}'.format(make_path_safe(path))
            else:
                csv_path = 'file:///{}'.format(make_path_safe(path))
            begin = time.time()
            nucleus_statement = '''
            USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM "{path}" as csvLine
            MATCH (n:{phone_name}:{corpus}:speech {{id: csvLine.vowel_id}})-[r:contained_by]->(w:{word_name}:{corpus}:speech)
            SET n :nucleus, n.syllable_position = 'nucleus'
            '''
            statement = nucleus_statement.format(path=csv_path,
                                         corpus=corpus_context.cypher_safe_name,
                                         word_name=corpus_context.word_name,
                                         phone_name=corpus_context.phone_name)
            corpus_context.execute_cypher(statement)
            print('Nucleus took: {} seconds'.format(time.time()-begin))

            begin = time.time()
            node_statement = '''
            USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM "{path}" as csvLine
            MERGE (s_type:syllable_type:{corpus} {{id: csvLine.type_id}})
            ON CREATE SET s_type.label = csvLine.label
            WITH s_type, csvLine
            CREATE (s:syllable:{corpus}:speech {{id: csvLine.id,
                                label: csvLine.label,
                                begin: toFloat(csvLine.begin), end: toFloat(csvLine.end)}}),
                    (s)-[:is_a]->(s_type)
            '''
            statement = node_statement.format(path=csv_path,
                                         corpus=corpus_context.cypher_safe_name,)
            corpus_context.execute_cypher(statement)
            print('Nodes took: {} seconds'.format(time.time()-begin))

            begin = time.time()
            rel_statement = '''
            USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM "{path}" as csvLine
            MATCH (n:{phone_name}:{corpus}:speech:nucleus {{id: csvLine.vowel_id}})-[:contained_by]->(w:{word_name}:{corpus}:speech),
                    (s:syllable:{corpus}:speech {{id: csvLine.id}}),
                    (n)-[:spoken_by]->(sp:Speaker),
                    (n)-[:spoken_in]->(d:Discourse)
            WITH n, w, csvLine, sp, d, s
            CREATE (s)-[:contained_by]->(w),
                    (n)-[:contained_by]->(s),
                    (s)-[:spoken_by]->(sp),
                    (s)-[:spoken_in]->(d)
            with csvLine, s
            MATCH (prev:syllable {{id:csvLine.prev_id}})
              CREATE (prev)-[:precedes]->(s)
            '''
            statement = rel_statement.format(path=csv_path,
                                         corpus=corpus_context.cypher_safe_name,
                                         word_name=corpus_context.word_name,
                                         phone_name=corpus_context.phone_name)
            corpus_context.execute_cypher(statement)
            print('Relationships took: {} seconds'.format(time.time()-begin))

            begin = time.time()
            del_rel_statement = '''
            USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM "{path}" as csvLine
            MATCH (n:{phone_name}:{corpus}:speech:nucleus {{id: csvLine.vowel_id}})-[r:contained_by]->(w:{word_name}:{corpus}:speech)
            DELETE r
            '''
            statement = del_rel_statement.format(path=csv_path,
                                         corpus=corpus_context.cypher_safe_name,
                                         word_name=corpus_context.word_name,
                                         phone_name=corpus_context.phone_name)
            corpus_context.execute_cypher(statement)
            print('Deleting relationships took: {} seconds'.format(time.time()-begin))

            begin = time.time()
            onset_statement = '''
            USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM "{path}" as csvLine
            MATCH (n:{phone_name}:nucleus:{corpus}:speech)-[:contained_by]->(s:syllable:{corpus}:speech {{id: csvLine.id}})-[:contained_by]->(w:{word_name}:{corpus}:speech)
            WITH csvLine, s, w, n
            OPTIONAL MATCH
                    (onset:{phone_name}:{corpus} {{id: csvLine.onset_id}}),
                    onspath = (onset)-[:precedes*1..10]->(n)
    
            with n, w,s, csvLine, onspath
            UNWIND (case when onspath is not null then nodes(onspath)[0..-1] else [null] end) as o
    
            OPTIONAL MATCH (o)-[r:contained_by]->(w)
            with n, w,s, csvLine, filter(x in collect(o) WHERE x is not NULL) as ons,
            filter(x in collect(r) WHERE x is not NULL) as rels
            FOREACH (o in ons | SET o :onset, o.syllable_position = 'onset')
            FOREACH (o in ons | CREATE (o)-[:contained_by]->(s))
            FOREACH (r in rels | DELETE r)
            '''
            statement = onset_statement.format(path=csv_path,
                                         corpus=corpus_context.cypher_safe_name,
                                         word_name=corpus_context.word_name,
                                         phone_name=corpus_context.phone_name)
            corpus_context.execute_cypher(statement)
            print('Onsets took: {} seconds'.format(time.time()-begin))

            begin = time.time()
            coda_statment = '''
            USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM "{path}" as csvLine
            MATCH (n:nucleus:{corpus}:speech)-[:contained_by]->(s:syllable:{corpus}:speech {{id: csvLine.id}})-[:contained_by]->(w:{word_name}:{corpus}:speech)
            WITH csvLine, s, w, n
            OPTIONAL MATCH
                    (coda:{phone_name}:{corpus} {{id: csvLine.coda_id}}),
                codapath = (n)-[:precedes*1..10]->(coda)
            WITH n, w, s, codapath
            UNWIND (case when codapath is not null then nodes(codapath)[1..] else [null] end) as c
    
            OPTIONAL MATCH (c)-[r:contained_by]->(w)
            WITH n, w,s, filter(x in collect(c) WHERE x is not NULL) as cod,
            filter(x in collect(r) WHERE x is not NULL) as rels
            FOREACH (c in cod | SET c :coda, c.syllable_position = 'coda')
            FOREACH (c in cod | CREATE (c)-[:contained_by]->(s))
            FOREACH (r in rels | DELETE r)
            '''
            statement = coda_statment.format(path=csv_path,
                                         corpus=corpus_context.cypher_safe_name,
                                         word_name=corpus_context.word_name,
                                         phone_name=corpus_context.phone_name)
            corpus_context.execute_cypher(statement)
            print('Codas took: {} seconds'.format(time.time()-begin))


def import_nonsyl_csv(corpus_context, call_back=None, stop_check=None):
    """
    Import a nonsyllable from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.syllabic.SyllabicContext`
        the corpus to load into
    split_name : str
        the identifier of the file
    """
    speakers = corpus_context.speakers
    if call_back is not None:
        call_back('Importing degenerate syllables...')
        call_back(0, len(speakers))
    corpus_context.execute_cypher('CREATE CONSTRAINT ON (node:syllable) ASSERT node.id IS UNIQUE')
    corpus_context.execute_cypher('CREATE CONSTRAINT ON (node:syllable_type) ASSERT node.id IS UNIQUE')
    corpus_context.execute_cypher('CREATE INDEX ON :syllable(begin)')
    corpus_context.execute_cypher('CREATE INDEX ON :syllable(end)')
    corpus_context.execute_cypher('CREATE INDEX ON :syllable(label)')
    corpus_context.execute_cypher('CREATE INDEX ON :syllable_type(label)')
    for i, s in enumerate(speakers):
        if stop_check is not None and stop_check():
            return
        if call_back is not None:
            call_back('Importing degenerate syllables for speaker {} of {} ({})...'.format(i, len(speakers), s))
            call_back(i)
        discourses = corpus_context.get_discourses_of_speaker(s)
        for d in discourses:
            path = os.path.join(corpus_context.config.temporary_directory('csv'),
                                '{}_{}_nonsyl.csv'.format(s, d))
            print('nonsyl', s, d, path)

            # If on the Docker version, the files live in /site/proj
            if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
                csv_path = 'file:///site/proj/{}'.format(make_path_safe(path))
            else:
                csv_path = 'file:///{}'.format(make_path_safe(path))

            begin = time.time()
            node_statement = '''USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM "{path}" as csvLine
            MERGE (s_type:syllable_type:{corpus} {{id: csvLine.type_id}})
            ON CREATE SET s_type.label = csvLine.label
            WITH s_type, csvLine
        CREATE (s:syllable:{corpus}:speech {{id: csvLine.id,
                                        begin: toFloat(csvLine.begin), end: toFloat(csvLine.end),
                                        label: csvLine.label}}),
                    (s)-[:is_a]->(s_type) 
            '''

            statement = node_statement.format(path=csv_path,
                                         corpus=corpus_context.cypher_safe_name,)
            corpus_context.execute_cypher(statement)
            print('Nodes took: {} seconds'.format(time.time()-begin))

            begin = time.time()
            rel_statement = '''
            USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM "{path}" as csvLine
        MATCH (o:{phone_name}:{corpus}:speech {{id: csvLine.onset_id}})-[r:contained_by]->(w:{word_name}:{corpus}:speech),
                    (o)-[:spoken_by]->(sp:Speaker),
                    (o)-[:spoken_in]->(d:Discourse),
                    (s:syllable:{corpus}:speech {{id: csvLine.id}})
            WITH w, csvLine, sp, d, s
            CREATE (s)-[:contained_by]->(w),
                    (s)-[:spoken_by]->(sp),
                    (s)-[:spoken_in]->(d)
            with csvLine, s
            OPTIONAL MATCH (prev:syllable {{id:csvLine.prev_id}})
            FOREACH (pv IN CASE WHEN prev IS NOT NULL THEN [prev] ELSE [] END |
              CREATE (pv)-[:precedes]->(s)
            )
            with csvLine, s
            OPTIONAL MATCH (foll:syllable {{prev_id:csvLine.id}})
            FOREACH (fv IN CASE WHEN foll IS NOT NULL THEN [foll] ELSE [] END |
              CREATE (s)-[:precedes]->(fv)
            )
            '''
            statement = rel_statement.format(path=csv_path,
                                         corpus=corpus_context.cypher_safe_name,
                                         word_name=corpus_context.word_name,
                                         phone_name=corpus_context.phone_name)
            corpus_context.execute_cypher(statement)
            print('Relationships took: {} seconds'.format(time.time()-begin))

            begin = time.time()
            phone_statement = '''USING PERIODIC COMMIT 2000
            LOAD CSV WITH HEADERS FROM "{path}" as csvLine
        MATCH (o:{phone_name}:{corpus}:speech {{id: csvLine.onset_id}}),
        (s:syllable:{corpus}:speech {{id: csvLine.id}})-[:contained_by]->(w:{word_name}:{corpus}:speech)
        with o, w, csvLine, s
        OPTIONAL MATCH
        (c:{phone_name}:{corpus}:speech {{id: csvLine.coda_id}})-[:contained_by]->(w),
        p = (o)-[:precedes*..10]->(c)
        with o, w, s, p, csvLine
            UNWIND (case when p is not null then nodes(p) else [o] end) as c
    
            OPTIONAL MATCH (c)-[r:contained_by]->(w)
            with w,s, toInt(csvLine.break) as break, filter(x in collect(c) WHERE x is not NULL) as cod,
            filter(x in collect(r) WHERE x is not NULL) as rels
            FOREACH (c in cod[break..] | SET c :coda, c.syllable_position = 'coda')
            FOREACH (c in cod[..break] | SET c :onset, c.syllable_position = 'onset')
            FOREACH (c in cod | CREATE (c)-[:contained_by]->(s))
            FOREACH (r in rels | DELETE r)
            '''
            statement = phone_statement.format(path=csv_path,
                                         corpus=corpus_context.cypher_safe_name,
                                         word_name=corpus_context.word_name,
                                         phone_name=corpus_context.phone_name)
            corpus_context.execute_cypher(statement)
            print('Phones took: {} seconds'.format(time.time()-begin))


def import_subannotation_csv(corpus_context, type, annotated_type, props):
    """
    Import a subannotation from csv file

    Parameters
    ----------
    corpus_context: :class:`~polyglotdb.corpus.AnnotatedContext`
        the corpus to load into
    type : str
        the file name of the csv
    annotated_type : obj

    props : list

    """
    path = os.path.join(corpus_context.config.temporary_directory('csv'),
                        '{}_subannotations.csv'.format(type))

    # If on the Docker version, the files live in /site/proj
    if os.path.exists('/site/proj') and not path.startswith('/site/proj'):
        csv_path = 'file:///site/proj/{}'.format(make_path_safe(path))
    else:
        csv_path = 'file:///{}'.format(make_path_safe(path))

    prop_temp = '''{name}: csvLine.{name}'''
    properties = []

    corpus_context.execute_cypher('CREATE CONSTRAINT ON (node:%s) ASSERT node.id IS UNIQUE' % type)

    for p in props:
        if p in ['id', 'annotated_id', 'begin', 'end']:
            continue
        properties.append(prop_temp.format(name=p))
    if properties:
        properties = ', ' + ', '.join(properties)
    else:
        properties = ''
    statement = '''CYPHER planner=rule USING PERIODIC COMMIT 500
    LOAD CSV WITH HEADERS FROM "{path}" AS csvLine
            MATCH (annotated:{a_type}:{corpus} {{id: csvLine.annotated_id}})
            CREATE (annotated) <-[:annotates]-(annotation:{type}:{corpus}
                {{id: csvLine.id, begin: toFloat(csvLine.begin),
                end: toFloat(csvLine.end){properties}}})
            '''
    statement = statement.format(path=csv_path,
                                 corpus=corpus_context.cypher_safe_name,
                                 a_type=annotated_type,
                                 type=type,
                                 properties=properties)
    corpus_context.execute_cypher(statement)
    for p in props:
        if p in ['id', 'annotated_id']:
            continue
        corpus_context.execute_cypher('CREATE INDEX ON :%s(%s)' % (type, p))
        # os.remove(path) # FIXME Neo4j 2.3 does not release files
